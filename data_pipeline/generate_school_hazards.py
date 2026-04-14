"""
AirScout Data Pipeline: School Zone Hazard Generator
=====================================================

School Zone Hard Rule: Between 7-9 AM and 2-4 PM on weekdays,
all school zones are HIGH RISK (severity 5) due to diesel bus idling.
"""

import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import school_zone

CHICAGO_TZ = ZoneInfo("America/Chicago")

MORNING_START = time(7, 0)
MORNING_END = time(9, 0)
AFTERNOON_START = time(14, 0)
AFTERNOON_END = time(16, 0)
PEAK_SEVERITY = school_zone.peak_severity
HAZARD_EXPIRATION_MINUTES = 30
ACTIVE_WEEKDAYS = school_zone.active_days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def is_school_zone_peak_time(dt: datetime = None) -> tuple[bool, str]:
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    if dt.weekday() not in ACTIVE_WEEKDAYS:
        return False, "weekend"
    current_time = dt.time()
    if MORNING_START <= current_time < MORNING_END:
        return True, "morning_dropoff"
    if AFTERNOON_START <= current_time < AFTERNOON_END:
        return True, "afternoon_pickup"
    return False, "off_peak"


def get_next_peak_time(dt: datetime = None) -> datetime:
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    current_time = dt.time()
    if current_time < MORNING_START and dt.weekday() in ACTIVE_WEEKDAYS:
        return dt.replace(hour=7, minute=0, second=0, microsecond=0)
    if current_time < AFTERNOON_START and dt.weekday() in ACTIVE_WEEKDAYS:
        return dt.replace(hour=14, minute=0, second=0, microsecond=0)
    days_ahead = 1
    next_day = dt + timedelta(days=days_ahead)
    while next_day.weekday() not in ACTIVE_WEEKDAYS:
        days_ahead += 1
        next_day = dt + timedelta(days=days_ahead)
    return next_day.replace(hour=7, minute=0, second=0, microsecond=0)


def generate_school_zone_hazards(engine, dry_run: bool = False) -> int:
    is_peak, period = is_school_zone_peak_time()

    if not is_peak:
        logger.info(f"Not in peak hours (current period: {period})")
        logger.info(f"Next peak time: {get_next_peak_time()}")
        if not dry_run:
            with engine.connect() as conn:
                result = conn.execute(text("DELETE FROM hazards_active WHERE type = 'SCHOOL'"))
                conn.commit()
                if result.rowcount > 0:
                    logger.info(f"Cleaned up {result.rowcount} expired school hazards")
        return 0

    logger.info(f"Peak time detected: {period}")
    logger.info("Generating HIGH RISK hazards for all school zones...")
    expires_at = datetime.now(CHICAGO_TZ) + timedelta(minutes=HAZARD_EXPIRATION_MINUTES)

    if dry_run:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM schools_static WHERE is_active = TRUE"))
            count = result.scalar()
            logger.info(f"Would create {count} school zone hazards (severity={PEAK_SEVERITY})")
            return count

    query = text("""
        INSERT INTO hazards_active (type, severity, location, description, source_id, expires_at, metadata)
        SELECT
            'SCHOOL', :severity, location,
            'School Zone - ' || school_name || ' (' || :period || ')',
            'SCHOOL-' || school_id, :expires_at,
            jsonb_build_object('school_id', school_id, 'school_name', school_name, 'school_type', school_type, 'period', :period, 'zone_radius_m', zone_radius_meters)
        FROM schools_static WHERE is_active = TRUE
        ON CONFLICT (source_id) DO UPDATE SET
            severity = EXCLUDED.severity, description = EXCLUDED.description,
            expires_at = EXCLUDED.expires_at, metadata = EXCLUDED.metadata, updated_at = NOW()
    """)

    with engine.connect() as conn:
        conn.execute(query, {"severity": PEAK_SEVERITY, "period": period, "expires_at": expires_at})
        conn.commit()
        count_result = conn.execute(text("SELECT COUNT(*) FROM hazards_active WHERE type = 'SCHOOL'"))
        count = count_result.scalar()

    logger.info(f"Created/updated {count} school zone hazards (severity={PEAK_SEVERITY})")
    return count


def run_school_hazard_generation(dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("AirScout School Zone Hazard Generator")
    logger.info("=" * 60)

    now = datetime.now(CHICAGO_TZ)
    logger.info(f"Current time (Chicago): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Day of week: {now.strftime('%A')}")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")

    try:
        engine = get_engine()
        count = generate_school_zone_hazards(engine, dry_run=dry_run)
        logger.info(f"Generation complete. Active school hazards: {count}")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AirScout School Zone Hazard Generator")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_school_hazard_generation(dry_run=args.dry_run)
