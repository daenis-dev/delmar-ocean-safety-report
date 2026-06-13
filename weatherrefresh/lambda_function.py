import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
import requests

LOGGER = logging.getLogger()
LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO"))

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
UPDATE_FUNCTION_NAME = os.getenv("UPDATE_FUNCTION_NAME", "delmar-weather-update-api")
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "12"))
USER_AGENT = os.getenv("USER_AGENT", "delmar-beaches-guide-weather-refresh/1.0")

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

BEACH_COORDS: Dict[str, Dict[str, Any]] = {
    "Rehoboth Beach": {"state": "DE", "latitude": 38.7209, "longitude": -75.0760},
    "Bethany Beach": {"state": "DE", "latitude": 38.5396, "longitude": -75.0552},
    "Ocean City": {"state": "MD", "latitude": 38.3365, "longitude": -75.0849},
}

CARDINALS = [
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
]

lambda_client = boto3.client("lambda", region_name=AWS_REGION)


def lambda_handler(event, context):
    try:
        beach_reports = [build_beach_report(name, cfg) for name, cfg in BEACH_COORDS.items()]
        payload = {"beachReports": beach_reports}

        invoke_update_lambda(payload)

        return build_json_response(200, {
            "message": "Success",
            "beachReportsCreated": len(beach_reports),
            "beaches": [report["beach"] for report in beach_reports],
        })
    except Exception as exc:
        LOGGER.exception("Error refreshing Delmar beach weather")
        return build_json_response(500, {"message": "Internal server error", "error": str(exc)})


def build_beach_report(beach_name: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    latitude = cfg["latitude"]
    longitude = cfg["longitude"]

    weather = fetch_weather(latitude, longitude)
    marine = fetch_marine(latitude, longitude)

    weather_hourly = weather.get("hourly") or {}
    marine_hourly = marine.get("hourly") or {}

    weather_times = weather_hourly.get("time") or []
    marine_times = marine_hourly.get("time") or []

    now = datetime.now(timezone.utc)
    weather_idx = latest_hour_index(weather_times, now)
    marine_idx = latest_hour_index(marine_times, now)

    wave_period = (
        safe_get(marine_hourly.get("wave_period") or [], marine_idx)
        or safe_get(marine_hourly.get("swell_wave_period") or [], marine_idx)
        or safe_get(marine_hourly.get("wind_wave_period") or [], marine_idx)
        or safe_get(marine_hourly.get("swell_wave_peak_period") or [], marine_idx)
        or safe_get(marine_hourly.get("wind_wave_peak_period") or [], marine_idx)
    )

    return {
        "beach": beach_name,
        "state": cfg["state"],
        "latitude": latitude,
        "longitude": longitude,
        "recordedAt": to_utc_z(now),
        "wind": {
            "currentSpeed": safe_get(weather_hourly.get("wind_speed_10m") or [], weather_idx),
            "currentDirection": degrees_to_cardinal(safe_get(weather_hourly.get("wind_direction_10m") or [], weather_idx)),
            "currentGust": safe_get(weather_hourly.get("wind_gusts_10m") or [], weather_idx),
            "forecastedSpeed2h": safe_get_offset(weather_hourly.get("wind_speed_10m") or [], weather_idx, 2),
            "forecastedDirection2h": degrees_to_cardinal(safe_get_offset(weather_hourly.get("wind_direction_10m") or [], weather_idx, 2)),
            "forecastedSpeed4h": safe_get_offset(weather_hourly.get("wind_speed_10m") or [], weather_idx, 4),
            "forecastedDirection4h": degrees_to_cardinal(safe_get_offset(weather_hourly.get("wind_direction_10m") or [], weather_idx, 4)),
            "forecastedSpeed6h": safe_get_offset(weather_hourly.get("wind_speed_10m") or [], weather_idx, 6),
            "forecastedDirection6h": degrees_to_cardinal(safe_get_offset(weather_hourly.get("wind_direction_10m") or [], weather_idx, 6)),
        },
        "rain": {
            "currentPrecipitation": safe_get(weather_hourly.get("precipitation") or [], weather_idx) or 0.0,
            "forecastedRain2h": sum_window(weather_hourly.get("precipitation") or [], weather_idx + 1, weather_idx + 2),
            "forecastedRain4h": sum_window(weather_hourly.get("precipitation") or [], weather_idx + 1, weather_idx + 4),
            "forecastedRain6h": sum_window(weather_hourly.get("precipitation") or [], weather_idx + 1, weather_idx + 6),
        },
        "marine": {
            "waveHeight": meters_to_feet(safe_get(marine_hourly.get("wave_height") or [], marine_idx)),
            "wavePeriod": wave_period,
            "waveDirection": degrees_to_cardinal(safe_get(marine_hourly.get("wave_direction") or [], marine_idx)),
            "windWaveHeight": meters_to_feet(safe_get(marine_hourly.get("wind_wave_height") or [], marine_idx)),
            "windWavePeriod": safe_get(marine_hourly.get("wind_wave_period") or [], marine_idx),
            "windWaveDirection": degrees_to_cardinal(safe_get(marine_hourly.get("wind_wave_direction") or [], marine_idx)),
            "swellWaveHeight": meters_to_feet(safe_get(marine_hourly.get("swell_wave_height") or [], marine_idx)),
            "swellWavePeriod": safe_get(marine_hourly.get("swell_wave_period") or [], marine_idx),
            "swellWaveDirection": degrees_to_cardinal(safe_get(marine_hourly.get("swell_wave_direction") or [], marine_idx)),
            "seaSurfaceTemperature": c_to_f(safe_get(marine_hourly.get("sea_surface_temperature") or [], marine_idx)),
        },
    }


def fetch_weather(latitude: float, longitude: float) -> Dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "forecast_days": 2,
    }
    return http_get_json(OPEN_METEO_FORECAST_URL, params)


def fetch_marine(latitude: float, longitude: float) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join([
            "wave_height",
            "wave_direction",
            "wave_period",
            "wind_wave_height",
            "wind_wave_direction",
            "wind_wave_period",
            "wind_wave_peak_period",
            "swell_wave_height",
            "swell_wave_direction",
            "swell_wave_period",
            "swell_wave_peak_period",
            "sea_surface_temperature",
        ]),
        "length_unit": "metric",
        "timezone": "UTC",
        "forecast_days": 2,
        "cell_selection": "sea",
    }

    return http_get_json(OPEN_METEO_MARINE_URL, params)


def http_get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    response = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def invoke_update_lambda(payload: Dict[str, Any]) -> None:
    update_function_url = os.getenv("UPDATE_FUNCTION_URL")

    if update_function_url:
        response = requests.post(
            update_function_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        LOGGER.info("Invoked local weather update via UPDATE_FUNCTION_URL")
        return

    invocation_type = os.getenv("UPDATE_INVOCATION_TYPE", "RequestResponse")

    response = lambda_client.invoke(
        FunctionName=UPDATE_FUNCTION_NAME,
        InvocationType=invocation_type,
        Payload=json.dumps(payload).encode("utf-8"),
    )

    if invocation_type == "RequestResponse":
        response_payload = response["Payload"].read().decode("utf-8")
        LOGGER.info("Weather update response: %s", response_payload)

        if response.get("FunctionError"):
            raise RuntimeError(f"Weather update Lambda failed: {response_payload}")

    LOGGER.info(
        "Invoked AWS weather update Lambda %s with InvocationType=%s",
        UPDATE_FUNCTION_NAME,
        invocation_type,
    )


def parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_hour_index(times: List[str], target: datetime) -> int:
    if not times:
        return 0
    best = 0
    for index, value in enumerate(times):
        if parse_time(value) <= target:
            best = index
        else:
            break
    return best


def safe_get(values: List[Any], index: int) -> Optional[Any]:
    if index < 0 or index >= len(values):
        return None
    return values[index]


def safe_get_offset(values: List[Any], index: int, offset: int) -> Optional[Any]:
    return safe_get(values, index + offset)


def sum_window(values: List[Any], start_index: int, end_index: int) -> float:
    total = 0.0
    for i in range(start_index, min(end_index + 1, len(values))):
        value = values[i]
        if value is not None:
            total += float(value)
    return round(total, 3)


def degrees_to_cardinal(degrees: Optional[float]) -> Optional[str]:
    if degrees is None:
        return None
    index = int((float(degrees) % 360 + 11.25) / 22.5) % 16
    return CARDINALS[index]


def meters_to_feet(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value) * 3.28084, 2)

def c_to_f(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round((float(value) * 9 / 5) + 32, 2)

def to_utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_json_response(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
