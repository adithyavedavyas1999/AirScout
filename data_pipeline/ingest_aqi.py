"""
AirScout Data Pipeline: EPA AirNow AQI Ingestion
==================================================

Fetches real-time Air Quality Index data from the EPA AirNow API
and creates AQI hazards when readings exceed healthy thresholds.
"""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import aqi as aqi_config

CHICAGO_TZ = ZoneInfo("America/Chicago")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


AQI_SEVERITY_MAP = [
    (301, 5, "Hazardous"),
    (201, 5, "Very Unhealthy"),
    (151, 4, "Unhealthy"),
    (101, 3, "Unhealthy for Sensitive Groups"),
    (51, 2, "Moderate"),
    (0, 1, "Good"),
]


def aqi_to_severity(aqi_value: int) -> tuple[int, str]:
    for threshold, severity, label in AQI_SEVERITY_MAP:
        if aqi_value >= threshold:
            return severity, label
    return 1, "Good"


def fetch_current_aqi() -> list[dict]:
    """Fetch current AQI observations from EPA AirNow for Chicago bbox."""
    if not aqi_config.api_key:
        logger.warning("AIRNOW_API_KEY not set - skipping AQI ingestion")
        return []

    url = f"{aqi_config.base_url}/observation/latLong/current/"
    bbox = aqi_config.bbox_chicago.split(",")
    params = {
        "format": "application/json",
        "latitude": "41.8781",
        "longitude": "-87.6298",
        "distance": "50",
        "API_KEY": aqi_config.api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"Fetched {len(data)} AQI observations")
        return data
    except Exception as e:
        logger.error(f"Error fetching AQI data: {e}")
        return []


def upsert_aqi_hazards(engine, observations: list[dict]) -> int:
    if not observations:
        return 0

    expires_at = datetime.now(CHICAGO_TZ) + timedelta(minutes=aqi_config.hazard_expiration_minutes)
    upserted_count = 0

    with engine.connect() as conn:
        for obs in observations:
            aqi_value = obs.get("AQI", 0)
            if aqi_value < aqi_config.min_aqi_for_hazard:
                continue

            lat = obs.get("Latitude")
            lon = obs.get("Longitude")
            if not lat or not lon:
                continue

            severity, category = aqi_to_severity(aqi_value)
            parameter = obs.get("ParameterName", "Unknown")
            reporting_area = obs.get("ReportingArea", "Chicago")

            wkt_point = f"SRID=4326;POINT({lon} {lat})"
            source_id = f"AQI-{reporting_area}-{parameter}".replace(" ", "_")
            description = f"Air Quality: {category} (AQI {aqi_value}, {parameter}) - {reporting_area}"

            metadata = {
                "aqi_value": aqi_value,
                "category": category,
                "parameter": parameter,
                "reporting_area": reporting_area,
                "observation_time": obs.get("DateObserved", ""),
            }

            query = text("""
                INSERT INTO hazards_active (type, severity, location, description, source_id, expires_at, metadata)
                VALUES ('AQI', :severity, ST_GeomFromText(:location, 4326), :description, :source_id, :expires_at, :metadata)
                ON CONFLICT (source_id) DO UPDATE SET
                    severity = EXCLUDED.severity, description = EXCLUDED.description,
                    expires_at = EXCLUDED.expires_at, metadata = EXCLUDED.metadata, updated_at = NOW()
            """)

            try:
                conn.execute(query, {
                    "severity": severity,
                    "location": wkt_point,
                    "description": description,
                    "source_id": source_id,
                    "expires_at": expires_at,
                    "metadata": json.dumps(metadata),
                })
                upserted_count += 1
            except Exception as e:
                logger.error(f"Error upserting AQI observation: {e}")
        conn.commit()

    logger.info(f"Upserted {upserted_count} AQI hazards")
    return upserted_count


def run_aqi_ingestion(dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("AirScout AQI Ingestion Pipeline")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)

    observations = fetch_current_aqi()
    if not observations:
        logger.info("No AQI data to process")
        return

    if dry_run:
        for obs in observations:
            aqi_val = obs.get("AQI", 0)
            severity, category = aqi_to_severity(aqi_val)
            logger.info(f"  AQI {aqi_val} ({category}) - {obs.get('ParameterName')} at {obs.get('ReportingArea')}")
        return

    engine = get_engine()
    upsert_aqi_hazards(engine, observations)
    logger.info("AQI ingestion completed successfully")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AirScout AQI Ingestion")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_aqi_ingestion(dry_run=args.dry_run)
