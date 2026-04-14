"""
AirScout Data Pipeline: Weather Data for Wind-Adjusted Scoring
================================================================

Fetches current wind speed/direction from OpenWeatherMap to amplify
hazard scores when wind carries particulate matter toward routes.
"""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import weather as weather_config

CHICAGO_TZ = ZoneInfo("America/Chicago")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_current_weather() -> dict | None:
    """Fetch current weather for Chicago from OpenWeatherMap."""
    if not weather_config.api_key:
        logger.warning("OPENWEATHER_API_KEY not set - skipping weather fetch")
        return None

    url = f"{weather_config.base_url}/weather"
    params = {
        "lat": weather_config.chicago_lat,
        "lon": weather_config.chicago_lon,
        "appid": weather_config.api_key,
        "units": "imperial",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        wind = data.get("wind", {})
        result = {
            "wind_speed_mph": wind.get("speed", 0),
            "wind_deg": wind.get("deg", 0),
            "wind_gust_mph": wind.get("gust", 0),
            "temp_f": data.get("main", {}).get("temp", 0),
            "humidity": data.get("main", {}).get("humidity", 0),
            "description": data.get("weather", [{}])[0].get("description", ""),
            "fetched_at": datetime.now(CHICAGO_TZ).isoformat(),
        }

        logger.info(f"Weather: {result['description']}, Wind: {result['wind_speed_mph']}mph @ {result['wind_deg']}deg")
        return result

    except Exception as e:
        logger.error(f"Error fetching weather: {e}")
        return None


def store_weather_context(engine, weather_data: dict) -> None:
    """Store current weather in a context table for scoring adjustments."""
    query = text("""
        INSERT INTO weather_context (city, data, fetched_at)
        VALUES ('chicago', :data, NOW())
        ON CONFLICT (city)
        DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW()
    """)
    with engine.connect() as conn:
        conn.execute(query, {"data": json.dumps(weather_data)})
        conn.commit()


def get_wind_amplifier(weather_data: dict | None) -> float:
    """
    Calculate a multiplier (1.0 - 2.0) for hazard scores based on wind.

    High wind spreads particulate matter further from demolition sites.
    """
    if not weather_data:
        return 1.0

    wind_speed = weather_data.get("wind_speed_mph", 0)
    threshold = weather_config.wind_speed_amplifier_threshold_mph

    if wind_speed <= threshold:
        return 1.0

    amplifier = 1.0 + min(1.0, (wind_speed - threshold) / threshold)
    return round(amplifier, 2)


def run_weather_update(dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("AirScout Weather Update")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)

    weather_data = fetch_current_weather()
    if not weather_data:
        return

    amplifier = get_wind_amplifier(weather_data)
    logger.info(f"Wind amplifier: {amplifier}x")

    if dry_run:
        logger.info(f"Weather data: {json.dumps(weather_data, indent=2)}")
        return

    engine = get_engine()
    store_weather_context(engine, weather_data)
    logger.info("Weather context stored successfully")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AirScout Weather Update")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_weather_update(dry_run=args.dry_run)
