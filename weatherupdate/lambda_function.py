import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import boto3
import psycopg

LOGGER = logging.getLogger()
LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO"))

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
DB_SECRET_ID = os.getenv("DB_SECRET_ID")
DB_SSLMODE = os.getenv("DB_SSLMODE", "require")
DB_CONNECT_TIMEOUT_SECONDS = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5"))

secrets_client = boto3.client("secretsmanager", region_name=AWS_REGION)

OCEAN_ACTIVITIES_ACTIVITY_ID = 1
DANGER_LEVEL_SAFE_ID = 1
DANGER_LEVEL_MODERATELY_DANGEROUS_ID = 2
DANGER_LEVEL_DANGEROUS_ID = 3
DANGER_LEVEL_EXTREMELY_DANGEROUS_ID = 4


@dataclass(frozen=True)
class WindReadingInput:
    beach: str
    town_id: int
    recorded_at: datetime
    current_speed: float
    current_direction: str
    current_gust: Optional[float]
    forecasted_speed_2h: Optional[float]
    forecasted_direction_2h: Optional[str]
    forecasted_speed_4h: Optional[float]
    forecasted_direction_4h: Optional[str]
    forecasted_speed_6h: Optional[float]
    forecasted_direction_6h: Optional[str]


@dataclass(frozen=True)
class RainReadingInput:
    beach: str
    town_id: int
    recorded_at: datetime
    current_precipitation: float
    forecasted_rain_2h: Optional[float]
    forecasted_rain_4h: Optional[float]
    forecasted_rain_6h: Optional[float]


@dataclass(frozen=True)
class SwellReadingInput:
    beach: str
    town_id: int
    recorded_at: datetime
    wave_height: Optional[float]
    wave_period: Optional[float]
    wave_direction: Optional[str]
    wind_wave_height: Optional[float]
    swell_wave_height: Optional[float]
    sea_surface_temperature: Optional[float]


@dataclass
class LatestBeachState:
    beach: str
    town_id: int
    recorded_at: datetime
    wind: Optional[WindReadingInput] = None
    rain: Optional[RainReadingInput] = None
    swell: Optional[SwellReadingInput] = None


@dataclass
class NormalizedWeatherUpdate:
    wind_readings: list[WindReadingInput] = field(default_factory=list)
    rain_readings: list[RainReadingInput] = field(default_factory=list)
    swell_readings: list[SwellReadingInput] = field(default_factory=list)
    latest_by_beach: dict[str, LatestBeachState] = field(default_factory=dict)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    try:
        body = parse_request_body(event)
        validate_request_shape(body)

        with get_connection() as conn:
            with conn.cursor() as cur:
                town_ids = load_town_ids_by_name(cur)
                normalized = normalize_weather_update(body, town_ids)
                persist_weather_readings(cur, normalized)
                created = create_ocean_activity_snapshots(cur, normalized)

        return build_json_response(200, {
            "message": "Success",
            "windReadingsInserted": len(normalized.wind_readings),
            "rainReadingsInserted": len(normalized.rain_readings),
            "swellReadingsInserted": len(normalized.swell_readings),
            "oceanActivitySnapshotsCreated": created,
        })

    except ValueError as exc:
        LOGGER.warning("Bad request: %s", exc)
        return build_json_response(400, {"message": str(exc)})
    except Exception as exc:
        LOGGER.exception("Error processing weather update")
        return build_json_response(500, {"message": "Internal server error", "error": str(exc)})


def parse_request_body(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object")
    if "body" not in event:
        return event
    body = event["body"]
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        return json.loads(body)
    raise ValueError("Unsupported API Gateway body format")


def validate_request_shape(body: dict[str, Any]) -> None:
    beach_reports = body.get("beachReports") or []
    if not isinstance(beach_reports, list):
        raise ValueError("beachReports must be a list")
    if not beach_reports:
        raise ValueError("No beachReports provided")


def parse_iso8601_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("recordedAt must be a non-empty ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_db_credentials():
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USERNAME"],
        "password": os.environ["DB_PASSWORD"],
    }


def get_connection():
    credentials = get_db_credentials()
    return psycopg.connect(
        host=credentials["host"],
        port=credentials["port"],
        dbname=credentials["dbname"],
        user=credentials["user"],
        password=credentials["password"],
        sslmode=DB_SSLMODE,
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
    )


def load_town_ids_by_name(cur) -> dict[str, int]:
    cur.execute("SELECT id, name FROM towns")
    return {name: town_id for town_id, name in cur.fetchall()}


def normalize_weather_update(body: dict[str, Any], town_ids: dict[str, int]) -> NormalizedWeatherUpdate:
    normalized = NormalizedWeatherUpdate()

    for index, raw in enumerate(body.get("beachReports") or []):
        if not isinstance(raw, dict):
            raise ValueError(f"beachReports[{index}] must be an object")

        beach_name = raw.get("beach")
        if beach_name not in town_ids:
            raise ValueError(f"Unknown beach: {beach_name}")

        town_id = town_ids[beach_name]
        recorded_at = parse_iso8601_timestamp(raw.get("recordedAt"))
        state = LatestBeachState(beach=beach_name, town_id=town_id, recorded_at=recorded_at)

        wind = normalize_wind_reading(index, beach_name, town_id, recorded_at, raw.get("wind"))
        normalized.wind_readings.append(wind)
        state.wind = wind

        rain = normalize_rain_reading(beach_name, town_id, recorded_at, raw.get("rain") or {})
        normalized.rain_readings.append(rain)
        state.rain = rain

        swell = normalize_swell_reading(beach_name, town_id, recorded_at, raw.get("marine") or {})
        normalized.swell_readings.append(swell)
        state.swell = swell

        normalized.latest_by_beach[beach_name] = state

    return normalized


def optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def normalize_wind_reading(index: int, beach_name: str, town_id: int, recorded_at: datetime, wind: Any) -> WindReadingInput:
    if not isinstance(wind, dict):
        raise ValueError(f"beachReports[{index}].wind is required")
    if wind.get("currentSpeed") is None:
        raise ValueError(f"beachReports[{index}].wind.currentSpeed is required")
    if not wind.get("currentDirection"):
        raise ValueError(f"beachReports[{index}].wind.currentDirection is required")

    return WindReadingInput(
        beach=beach_name,
        town_id=town_id,
        recorded_at=recorded_at,
        current_speed=float(wind["currentSpeed"]),
        current_direction=wind["currentDirection"],
        current_gust=optional_float(wind.get("currentGust")),
        forecasted_speed_2h=optional_float(wind.get("forecastedSpeed2h")),
        forecasted_direction_2h=wind.get("forecastedDirection2h"),
        forecasted_speed_4h=optional_float(wind.get("forecastedSpeed4h")),
        forecasted_direction_4h=wind.get("forecastedDirection4h"),
        forecasted_speed_6h=optional_float(wind.get("forecastedSpeed6h")),
        forecasted_direction_6h=wind.get("forecastedDirection6h"),
    )


def normalize_rain_reading(beach_name: str, town_id: int, recorded_at: datetime, rain: dict[str, Any]) -> RainReadingInput:
    return RainReadingInput(
        beach=beach_name,
        town_id=town_id,
        recorded_at=recorded_at,
        current_precipitation=float(rain.get("currentPrecipitation") or 0.0),
        forecasted_rain_2h=optional_float(rain.get("forecastedRain2h")),
        forecasted_rain_4h=optional_float(rain.get("forecastedRain4h")),
        forecasted_rain_6h=optional_float(rain.get("forecastedRain6h")),
    )


def normalize_swell_reading(beach_name: str, town_id: int, recorded_at: datetime, marine: dict[str, Any]) -> SwellReadingInput:
    return SwellReadingInput(
        beach=beach_name,
        town_id=town_id,
        recorded_at=recorded_at,
        wave_height=optional_float(marine.get("waveHeight")),
        wave_period=optional_float(marine.get("wavePeriod")),
        wave_direction=marine.get("waveDirection"),
        wind_wave_height=optional_float(marine.get("windWaveHeight")),
        swell_wave_height=optional_float(marine.get("swellWaveHeight")),
        sea_surface_temperature=optional_float(marine.get("seaSurfaceTemperature")),
    )


def persist_weather_readings(cur, normalized: NormalizedWeatherUpdate) -> None:
    insert_wind_readings(cur, normalized.wind_readings)
    insert_rain_readings(cur, normalized.rain_readings)
    insert_swell_readings(cur, normalized.swell_readings)


def insert_wind_readings(cur, readings: list[WindReadingInput]) -> None:
    cur.executemany(
        """
        INSERT INTO wind_readings (
            recorded_at, town_id, current_speed, current_direction,
            forecasted_speed_2h, forecasted_direction_2h,
            forecasted_speed_4h, forecasted_direction_4h,
            forecasted_speed_6h, forecasted_direction_6h
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                r.recorded_at, r.town_id, r.current_speed, r.current_direction,
                r.forecasted_speed_2h, r.forecasted_direction_2h,
                r.forecasted_speed_4h, r.forecasted_direction_4h,
                r.forecasted_speed_6h, r.forecasted_direction_6h,
            )
            for r in readings
        ],
    )


def insert_rain_readings(cur, readings: list[RainReadingInput]) -> None:
    cur.executemany(
        """
        INSERT INTO rain_readings (
            recorded_at, town_id, accumulated_rainfall_past_1_hour, accumulated_rainfall_past_3_hours,
            forecasted_rain_2h, forecasted_rain_4h, forecasted_rain_6h,
            accumulated_rainfall_past_6_hours, accumulated_rainfall_past_12_hours, accumulated_rainfall_past_24_hours
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [(r.recorded_at, r.town_id, r.current_precipitation, r.current_precipitation, r.forecasted_rain_2h, r.forecasted_rain_4h, r.forecasted_rain_6h, r.current_precipitation, r.current_precipitation, r.current_precipitation) for r in readings],
    )


def insert_swell_readings(cur, readings: list[SwellReadingInput]) -> None:
    cur.executemany(
        """
        INSERT INTO swell_readings (
            recorded_at, town_id, height, period, direction
        ) VALUES (%s, %s, %s, %s, %s)
        """,
        [(r.recorded_at, r.town_id, r.wave_height or 0.0, r.wave_period, r.wave_direction) for r in readings],
    )


def create_ocean_activity_snapshots(cur, normalized: NormalizedWeatherUpdate) -> int:
    created = 0
    for beach_state in normalized.latest_by_beach.values():
        snapshot = determine_ocean_activity_snapshot(beach_state)
        cur.execute(
            """
            INSERT INTO activity_snapshots (
                recreational_activity_id, activity_summary, danger_level_id,
                explanation, recorded_at, town_id
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                OCEAN_ACTIVITIES_ACTIVITY_ID,
                snapshot["activity_summary"],
                snapshot["danger_level_id"],
                snapshot["explanation"],
                beach_state.recorded_at,
                beach_state.town_id,
            ),
        )
        created += 1
    return created


def determine_ocean_activity_snapshot(state: LatestBeachState) -> dict[str, Any]:
    wind = state.wind
    rain = state.rain
    swell = state.swell

    wind_speed = wind.current_speed if wind else 0.0
    wind_gust = wind.current_gust or wind_speed if wind else 0.0
    wave_height = swell.wave_height if swell and swell.wave_height is not None else 0.0
    wave_period = swell.wave_period if swell and swell.wave_period is not None else 0.0
    wind_wave_height = swell.wind_wave_height if swell and swell.wind_wave_height is not None else 0.0
    rain_now = rain.current_precipitation if rain else 0.0
    rain_6h = rain.forecasted_rain_6h if rain and rain.forecasted_rain_6h is not None else 0.0

    score = 0
    reasons: list[str] = []

    if wave_height >= 8:
        score = max(score, 4)
        reasons.append(f"surf is very large at about {wave_height:.1f} ft")
    elif wave_height >= 6:
        score = max(score, 3)
        reasons.append(f"surf is elevated at about {wave_height:.1f} ft")
    elif wave_height >= 4:
        score = max(score, 2)
        reasons.append(f"surf is moderate at about {wave_height:.1f} ft")
    else:
        reasons.append(f"surf is around {wave_height:.1f} ft")

    if wave_period >= 12 and wave_height >= 4:
        score = max(score, 3)
        reasons.append(f"longer-period energy near {wave_period:.0f}s can create stronger breaking waves and rip-current risk")
    elif wave_period >= 10 and wave_height >= 3:
        score = max(score, 2)
        reasons.append(f"wave period near {wave_period:.0f}s adds some extra power")

    if wind_speed >= 30 or wind_gust >= 38:
        score = max(score, 4)
        reasons.append(f"winds are very strong near {wind_speed:.0f} mph with gusts near {wind_gust:.0f} mph")
    elif wind_speed >= 22 or wind_gust >= 30:
        score = max(score, 3)
        reasons.append(f"winds are strong near {wind_speed:.0f} mph")
    elif wind_speed >= 15 or wind_wave_height >= 3:
        score = max(score, 2)
        reasons.append(f"winds are breezy near {wind_speed:.0f} mph and may make the water choppy")

    if rain_now >= 0.25 or rain_6h >= 0.75:
        score = max(score, 3)
        reasons.append("rain may reduce visibility and contribute to poor nearshore water quality")
    elif rain_now > 0 or rain_6h >= 0.25:
        score = max(score, 2)
        reasons.append("some rain is expected, which can reduce comfort and visibility")

    if score >= 4:
        danger_level_id = DANGER_LEVEL_EXTREMELY_DANGEROUS_ID
        summary = "Avoid ocean activities today"
        lead = "Conditions are not appropriate for casual swimming, surfing, bodyboarding, or wading."
    elif score == 3:
        danger_level_id = DANGER_LEVEL_DANGEROUS_ID
        summary = "Ocean activities are risky today"
        lead = "Only experienced ocean users should consider entering the water, and guarded beaches are strongly preferred."
    elif score == 2:
        danger_level_id = DANGER_LEVEL_MODERATELY_DANGEROUS_ID
        summary = "Use caution for ocean activities"
        lead = "Conditions may be manageable for stronger swimmers, but caution is needed."
    else:
        danger_level_id = DANGER_LEVEL_SAFE_ID
        summary = "Generally favorable for ocean activities"
        lead = "Conditions look relatively calm, but always check lifeguard flags and local beach warnings."

    if not reasons:
        reasons.append("no major ocean hazards were detected from the latest weather and marine data")

    explanation = f"{lead} Main factors: " + "; ".join(reasons) + "."

    return {
        "activity_summary": summary,
        "danger_level_id": danger_level_id,
        "explanation": explanation,
    }


def build_json_response(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": json.dumps(body),
    }
