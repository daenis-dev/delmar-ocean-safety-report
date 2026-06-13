import json
import logging
import os

import boto3
import psycopg
from psycopg.rows import dict_row

LOGGER = logging.getLogger()
LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO"))

DB_SSLMODE = os.getenv("DB_SSLMODE", "require")
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5"))

LATEST_ACTIVITY_SAFETY_QUERY = """
SELECT
    ra.name,
    a.activity_summary AS weather_summary,
    dl.name AS danger_level,
    a.explanation,
    TO_CHAR(a.recorded_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS recorded_at,
    t.name AS town_name
FROM (
    SELECT DISTINCT ON (a.recreational_activity_id, a.town_id)
        a.*
    FROM activity_snapshots a
    JOIN recreational_activities ra_filter ON ra_filter.id = a.recreational_activity_id
    JOIN towns t_filter ON t_filter.id = a.town_id
    WHERE ra_filter.name = 'Ocean Activities'
      AND t_filter.name IN ('Rehoboth Beach', 'Bethany Beach', 'Ocean City')
    ORDER BY a.recreational_activity_id, a.town_id, a.recorded_at DESC
) a
JOIN recreational_activities ra ON ra.id = a.recreational_activity_id
JOIN danger_levels dl ON dl.id = a.danger_level_id
JOIN towns t ON t.id = a.town_id
ORDER BY
    CASE t.name
        WHEN 'Rehoboth Beach' THEN 1
        WHEN 'Bethany Beach' THEN 2
        WHEN 'Ocean City' THEN 3
        ELSE 99
    END,
    t.name
"""

def get_db_credentials():
    local_secret_path = os.getenv("LOCAL_DB_SECRET_PATH")

    if local_secret_path:
        with open(local_secret_path, "r") as f:
            secret = json.load(f)

        return {
            "host": secret["host"],
            "port": int(secret.get("port", 5432)),
            "dbname": secret["dbname"],
            "user": secret["username"],
            "password": secret["password"],
        }

    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USERNAME"],
        "password": os.environ["DB_PASSWORD"],
    }

def get_connection():
    creds = get_db_credentials()
    return psycopg.connect(
        host=creds["host"],
        port=creds["port"],
        dbname=creds["dbname"],
        user=creds["user"],
        password=creds["password"],
        sslmode=DB_SSLMODE,
        connect_timeout=DB_CONNECT_TIMEOUT,
        row_factory=dict_row,
    )


def response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def normalize_activity_row(row: dict) -> dict:
    return {
        "name": row["name"],
        "weatherSummary": row["weather_summary"],
        "dangerLevel": row["danger_level"],
        "explanation": row["explanation"],
        "recordedAt": row["recorded_at"],
        "beachName": row["town_name"],
        "townName": row["town_name"],
    }


def lambda_handler(event, context):
    try:
        http_method = None
        if isinstance(event, dict):
            http_method = event.get("httpMethod")
            if http_method is None:
                request_context = event.get("requestContext") or {}
                http_method = (request_context.get("http") or {}).get("method")

        if http_method == "OPTIONS":
            return response(200, {"message": "OK"})

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(LATEST_ACTIVITY_SAFETY_QUERY)
                rows = cur.fetchall()

        activities = [normalize_activity_row(row) for row in rows]
        return response(200, {"message": "Success", "activities": activities, "count": len(activities)})

    except ValueError as e:
        LOGGER.warning("Bad request: %s", str(e))
        return response(400, {"message": str(e)})
    except Exception as e:
        LOGGER.exception("Error fetching ocean activity safety data")
        return response(500, {"message": "Internal server error", "error": str(e)})
