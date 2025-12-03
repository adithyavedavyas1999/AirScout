"""
AirScout Data Pipeline: School Zone Hazard Generator
=====================================================

Implements the "School Zone Hard Rule" from the PRD:
- Between 7-9 AM and 2-4 PM on weekdays, all school zones are HIGH RISK
- This overrides any traffic API data
- Severity is hard-coded to 5 (maximum) due to diesel bus idling

This script should run every 15 minutes via GitHub Actions to update
school zone hazards based on current time.

Author: AirScout Team
License: MIT
"""

import os
import json
import logging
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

from sqlalchemy import create_engine, text

# ============================================================
# Configuration
# ============================================================

# Chicago timezone
CHICAGO_TZ = ZoneInfo("America/Chicago")

# School zone peak hours (from PRD)
MORNING_START = time(7, 0)   # 7:00 AM
MORNING_END = time(9, 0)     # 9:00 AM
AFTERNOON_START = time(14, 0)  # 2:00 PM
AFTERNOON_END = time(16, 0)    # 4:00 PM

# Hard-coded severity during peak hours
PEAK_SEVERITY = 5  # Maximum severity

# School zone hazards expire after 30 minutes (re-generated every 15 min)
HAZARD_EXPIRATION_MINUTES = 30

# Active weekdays (Monday=0, Sunday=6)
ACTIVE_WEEKDAYS = [0, 1, 2, 3, 4]  # Monday through Friday

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# ============================================================
# Database Connection
# ============================================================

def get_database_url() -> str:
    """Build Supabase PostgreSQL connection URL from environment."""
    host = os.environ.get("SUPABASE_DB_HOST")
    port = os.environ.get("SUPABASE_DB_PORT", "5432")
    dbname = os.environ.get("SUPABASE_DB_NAME", "postgres")
    user = os.environ.get("SUPABASE_DB_USER", "postgres")
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    
    if not host or not password:
        raise ValueError(
            "Missing required environment variables: "
            "SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD must be set"
        )
    
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def get_engine():
    """Create SQLAlchemy engine."""
    return create_engine(get_database_url(), echo=False)


# ============================================================
# Time Logic
# ============================================================

def is_school_zone_peak_time(dt: datetime = None) -> tuple[bool, str]:
    """
    Check if the current time is during school zone peak hours.
    
    Args:
        dt: Datetime to check (defaults to now in Chicago timezone)
    
    Returns:
        Tuple of (is_peak_time, period_name)
    """
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    
    # Check if it's a weekday
    if dt.weekday() not in ACTIVE_WEEKDAYS:
        return False, "weekend"
    
    current_time = dt.time()
    
    # Check morning peak
    if MORNING_START <= current_time < MORNING_END:
        return True, "morning_dropoff"
    
    # Check afternoon peak
    if AFTERNOON_START <= current_time < AFTERNOON_END:
        return True, "afternoon_pickup"
    
    return False, "off_peak"


def get_next_peak_time(dt: datetime = None) -> datetime:
    """Get the next school zone peak time."""
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    
    current_time = dt.time()
    
    # If before morning peak today
    if current_time < MORNING_START and dt.weekday() in ACTIVE_WEEKDAYS:
        return dt.replace(hour=7, minute=0, second=0, microsecond=0)
    
    # If before afternoon peak today
    if current_time < AFTERNOON_START and dt.weekday() in ACTIVE_WEEKDAYS:
        return dt.replace(hour=14, minute=0, second=0, microsecond=0)
    
    # Find next weekday
    days_ahead = 1
    next_day = dt + timedelta(days=days_ahead)
    while next_day.weekday() not in ACTIVE_WEEKDAYS:
        days_ahead += 1
        next_day = dt + timedelta(days=days_ahead)
    
    return next_day.replace(hour=7, minute=0, second=0, microsecond=0)


# ============================================================
# Hazard Generation
# ============================================================

def generate_school_zone_hazards(engine, dry_run: bool = False) -> int:
    """
    Generate hazards for all school zones during peak hours.
    
    This creates a SCHOOL type hazard for each school in schools_static,
    with maximum severity (5) due to diesel idling from buses.
    
    Args:
        engine: SQLAlchemy engine
        dry_run: If True, don't write to database
    
    Returns:
        Number of hazards created
    """
    is_peak, period = is_school_zone_peak_time()
    
    if not is_peak:
        logger.info(f"Not in peak hours (current period: {period})")
        logger.info(f"Next peak time: {get_next_peak_time()}")
        
        # Clean up any existing school hazards when not in peak time
        if not dry_run:
            with engine.connect() as conn:
                result = conn.execute(text(
                    "DELETE FROM hazards_active WHERE type = 'SCHOOL'"
                ))
                conn.commit()
                if result.rowcount > 0:
                    logger.info(f"Cleaned up {result.rowcount} expired school hazards")
        return 0
    
    logger.info(f"🏫 Peak time detected: {period}")
    logger.info(f"Generating HIGH RISK hazards for all school zones...")
    
    # Calculate expiration
    expires_at = datetime.now(CHICAGO_TZ) + timedelta(minutes=HAZARD_EXPIRATION_MINUTES)
    
    if dry_run:
        # Just count schools
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM schools_static WHERE is_active = TRUE"))
            count = result.scalar()
            logger.info(f"Would create {count} school zone hazards (severity={PEAK_SEVERITY})")
            return count
    
    # Insert hazards for all active schools
    query = text("""
        INSERT INTO hazards_active (type, severity, location, description, source_id, expires_at, metadata)
        SELECT 
            'SCHOOL' as type,
            :severity as severity,
            location,
            'School Zone - ' || school_name || ' (' || :period || ')' as description,
            'SCHOOL-' || school_id as source_id,
            :expires_at as expires_at,
            jsonb_build_object(
                'school_id', school_id,
                'school_name', school_name,
                'school_type', school_type,
                'period', :period,
                'zone_radius_m', zone_radius_meters
            ) as metadata
        FROM schools_static
        WHERE is_active = TRUE
        ON CONFLICT (source_id) 
        DO UPDATE SET
            severity = EXCLUDED.severity,
            description = EXCLUDED.description,
            expires_at = EXCLUDED.expires_at,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {
            "severity": PEAK_SEVERITY,
            "period": period,
            "expires_at": expires_at,
        })
        conn.commit()
        
        # Get count of active school hazards
        count_result = conn.execute(text(
            "SELECT COUNT(*) FROM hazards_active WHERE type = 'SCHOOL'"
        ))
        count = count_result.scalar()
    
    logger.info(f"✅ Created/updated {count} school zone hazards (severity={PEAK_SEVERITY})")
    return count


# ============================================================
# Main Pipeline
# ============================================================

def run_school_hazard_generation(dry_run: bool = False):
    """
    Main entry point for school zone hazard generation.
    
    This should be run every 15 minutes via CRON to update school zone
    hazards based on current time.
    """
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
        
        logger.info("=" * 60)
        logger.info(f"Generation complete. Active school hazards: {count}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AirScout School Zone Hazard Generator")
    parser.add_argument("--dry-run", action="store_true", help="Run without database writes")
    
    args = parser.parse_args()
    run_school_hazard_generation(dry_run=args.dry_run)

