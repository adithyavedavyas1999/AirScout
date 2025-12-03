"""
AirScout Data Pipeline: Traffic Congestion Ingestion
=====================================================

Fetches Chicago traffic congestion data and creates TRAFFIC hazards.
Implements the "School Zone Override" - traffic data near schools is
IGNORED during peak hours (7-9 AM, 2-4 PM) as school hazards take priority.

Data Source:
- Chicago Traffic Tracker: https://data.cityofchicago.org/resource/85ca-t3if.json

Author: AirScout Team
License: MIT
"""

import os
import json
import logging
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString
from shapely import wkt
from sodapy import Socrata
from sqlalchemy import create_engine, text

# ============================================================
# Configuration
# ============================================================

CHICAGO_DATA_PORTAL = "data.cityofchicago.org"

# Chicago Traffic Tracker - Historical Congestion Estimates by Segment
# Dataset: https://data.cityofchicago.org/Transportation/Chicago-Traffic-Tracker-Historical-Congestion-Esti/sxs8-h27x
TRAFFIC_DATASET_ID = "sxs8-h27x"

# Chicago timezone
CHICAGO_TZ = ZoneInfo("America/Chicago")

# School zone override parameters
MORNING_START = time(7, 0)
MORNING_END = time(9, 0)
AFTERNOON_START = time(14, 0)
AFTERNOON_END = time(16, 0)
SCHOOL_ZONE_OVERRIDE_RADIUS_METERS = 200  # Ignore traffic within 200m of schools during peak

# Traffic severity mapping (based on congestion level)
# Speed ratio < 0.5 = severe congestion
SEVERITY_MAP = {
    "severe": 5,      # Speed < 25% of limit
    "heavy": 4,       # Speed 25-50% of limit
    "moderate": 3,    # Speed 50-75% of limit
    "light": 2,       # Speed 75-90% of limit
    "free_flow": 1,   # Speed > 90% of limit
}

# Minimum congestion level to create hazard
MIN_SEVERITY_THRESHOLD = 3  # Only create hazards for moderate+ congestion

# Hazard expiration
HAZARD_EXPIRATION_MINUTES = 30

# Logging
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

def is_school_zone_peak_time(dt: datetime = None) -> bool:
    """Check if current time is during school peak hours."""
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    
    # Only weekdays
    if dt.weekday() > 4:
        return False
    
    current_time = dt.time()
    return (MORNING_START <= current_time < MORNING_END or 
            AFTERNOON_START <= current_time < AFTERNOON_END)


# ============================================================
# Data Fetching
# ============================================================

def fetch_traffic_data(client: Socrata, limit: int = 2000) -> pd.DataFrame:
    """
    Fetch current traffic congestion data from Chicago Traffic Tracker.
    
    Dataset: https://data.cityofchicago.org/Transportation/Chicago-Traffic-Tracker-Historical-Congestion-Esti/sxs8-h27x
    
    Returns:
        DataFrame with traffic segments and congestion levels
    """
    logger.info("Fetching traffic congestion data...")
    
    try:
        # Fetch all columns - schema may vary
        results = client.get(
            TRAFFIC_DATASET_ID,
            limit=limit,
            order="time DESC"  # Get most recent data first
        )
        
        if not results:
            logger.warning("No traffic data found")
            return pd.DataFrame()
        
        df = pd.DataFrame.from_records(results)
        
        # Log available columns for debugging
        logger.info(f"Available columns: {list(df.columns)}")
        
        # Map various possible column names to standard names
        column_mappings = {
            # Segment ID
            "segmentid": "segmentid",
            "segment_id": "segmentid",
            "_id": "segmentid",
            
            # Street name
            "street": "street",
            "_traffic": "street",
            "street_name": "street",
            
            # Direction
            "_direction": "_direction",
            "direction": "_direction",
            
            # Speed - try various column names
            "current_speed": "current_speed",
            "speed": "current_speed",
            "bus_count": "current_speed",  # Some datasets use bus count as proxy
            
            # Coordinates
            "start_lon": "start_lon",
            "west": "start_lon",
            "_lif_lon": "start_lon",
            
            "start_lat": "start_lat", 
            "south": "start_lat",
            "_lif_lat": "start_lat",
            
            # From/To streets
            "_fromst": "_fromst",
            "from_street": "_fromst",
            
            "_tost": "_tost",
            "to_street": "_tost",
        }
        
        # Apply mappings
        rename_dict = {}
        for old_col, new_col in column_mappings.items():
            if old_col in df.columns and new_col not in rename_dict.values():
                rename_dict[old_col] = new_col
        
        if rename_dict:
            df = df.rename(columns=rename_dict)
        
        # Try to get coordinates from various possible columns
        if "start_lat" not in df.columns:
            for col in ["latitude", "lat", "south", "_lif_lat"]:
                if col in df.columns:
                    df["start_lat"] = df[col]
                    break
        
        if "start_lon" not in df.columns:
            for col in ["longitude", "lon", "long", "west", "_lif_lon"]:
                if col in df.columns:
                    df["start_lon"] = df[col]
                    break
        
        # Ensure we have segmentid
        if "segmentid" not in df.columns:
            df["segmentid"] = df.index.astype(str)
        
        # Ensure we have street name
        if "street" not in df.columns:
            street_cols = [c for c in df.columns if "street" in c.lower() or "road" in c.lower()]
            if street_cols:
                df["street"] = df[street_cols[0]]
            else:
                df["street"] = "Unknown Street"
        
        # Convert numeric columns
        numeric_cols = ["current_speed", "start_lon", "start_lat", "end_lon", "end_lat"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        # Check if we have required columns
        if "start_lat" not in df.columns or "start_lon" not in df.columns:
            logger.error("Could not find coordinate columns in traffic data")
            logger.error(f"Available columns: {list(df.columns)}")
            return pd.DataFrame()
        
        # Filter segments with valid coordinates
        df = df.dropna(subset=["start_lat", "start_lon"])
        
        if df.empty:
            logger.warning("No traffic data with valid coordinates")
            return pd.DataFrame()
        
        # Calculate congestion severity
        # If we have speed data, use it; otherwise assign moderate severity
        ASSUMED_SPEED_LIMIT = 30
        
        if "current_speed" in df.columns and df["current_speed"].notna().any():
            df["speed_ratio"] = df["current_speed"] / ASSUMED_SPEED_LIMIT
            
            def classify_congestion(ratio):
                if pd.isna(ratio) or ratio <= 0:
                    return "severe"
                elif ratio < 0.25:
                    return "severe"
                elif ratio < 0.5:
                    return "heavy"
                elif ratio < 0.75:
                    return "moderate"
                elif ratio < 0.9:
                    return "light"
                else:
                    return "free_flow"
            
            df["congestion_level"] = df["speed_ratio"].apply(classify_congestion)
        else:
            # No speed data - use moderate as default
            logger.warning("No speed data available - using default severity")
            df["congestion_level"] = "moderate"
            df["speed_ratio"] = 0.5
        
        df["severity"] = df["congestion_level"].map(SEVERITY_MAP)
        
        # Filter to only significant congestion
        df = df[df["severity"] >= MIN_SEVERITY_THRESHOLD]
        
        logger.info(f"Fetched {len(df)} congested traffic segments")
        return df
        
    except Exception as e:
        logger.error(f"Error fetching traffic data: {e}")
        raise


def fetch_school_locations(engine) -> gpd.GeoDataFrame:
    """Fetch school locations from database for school zone filtering."""
    query = """
        SELECT school_id, school_name, 
               ST_X(location::geometry) as lon, 
               ST_Y(location::geometry) as lat,
               zone_radius_meters
        FROM schools_static 
        WHERE is_active = TRUE
    """
    
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    
    if df.empty:
        return gpd.GeoDataFrame()
    
    geometry = [Point(row["lon"], row["lat"]) for _, row in df.iterrows()]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    
    return gdf


# ============================================================
# School Zone Override Logic
# ============================================================

def filter_traffic_near_schools(
    traffic_df: pd.DataFrame,
    schools_gdf: gpd.GeoDataFrame,
    radius_meters: float = SCHOOL_ZONE_OVERRIDE_RADIUS_METERS
) -> pd.DataFrame:
    """
    Remove traffic hazards that are within school zones during peak hours.
    
    This implements the PRD requirement: "Ignore API traffic data near schools
    between 7-9 AM and 2-4 PM."
    
    Args:
        traffic_df: DataFrame of traffic segments
        schools_gdf: GeoDataFrame of school locations
        radius_meters: Distance threshold for school zone override
    
    Returns:
        Filtered DataFrame with traffic near schools removed
    """
    if not is_school_zone_peak_time():
        logger.info("Not in school peak hours - no school zone filtering applied")
        return traffic_df
    
    if traffic_df.empty or schools_gdf.empty:
        return traffic_df
    
    logger.info(f"🏫 School zone override active - filtering traffic within {radius_meters}m of schools")
    
    # Create traffic GeoDataFrame
    traffic_geometry = [
        Point(row["start_lon"], row["start_lat"]) 
        for _, row in traffic_df.iterrows()
    ]
    traffic_gdf = gpd.GeoDataFrame(
        traffic_df, 
        geometry=traffic_geometry, 
        crs="EPSG:4326"
    )
    
    # Project to meters for accurate distance calculation
    traffic_projected = traffic_gdf.to_crs("EPSG:26971")
    schools_projected = schools_gdf.to_crs("EPSG:26971")
    
    # Create buffer around schools
    schools_projected["buffer"] = schools_projected.geometry.buffer(radius_meters)
    school_zones = schools_projected.set_geometry("buffer").unary_union
    
    # Filter out traffic points within school zones
    traffic_projected["near_school"] = traffic_projected.geometry.within(school_zones)
    
    filtered = traffic_projected[~traffic_projected["near_school"]]
    removed_count = len(traffic_projected) - len(filtered)
    
    logger.info(f"Removed {removed_count} traffic segments near schools")
    
    # Convert back to regular DataFrame (drop geometry)
    return pd.DataFrame(filtered.drop(columns=["geometry", "near_school"]))


# ============================================================
# Database Operations
# ============================================================

def upsert_traffic_hazards(engine, traffic_df: pd.DataFrame) -> int:
    """
    Insert or update traffic hazards in the database.
    
    Args:
        engine: SQLAlchemy engine
        traffic_df: DataFrame of traffic segments
    
    Returns:
        Number of hazards upserted
    """
    if traffic_df.empty:
        logger.info("No traffic hazards to upsert")
        return 0
    
    logger.info(f"Upserting {len(traffic_df)} traffic hazards...")
    
    expires_at = datetime.now(CHICAGO_TZ) + timedelta(minutes=HAZARD_EXPIRATION_MINUTES)
    upserted_count = 0
    
    with engine.connect() as conn:
        for _, row in traffic_df.iterrows():
            wkt_point = f"SRID=4326;POINT({row['start_lon']} {row['start_lat']})"
            
            metadata = {
                "segment_id": row.get("segmentid"),
                "street": row.get("street"),
                "direction": row.get("_direction"),
                "from_street": row.get("_fromst"),
                "to_street": row.get("_tost"),
                "current_speed": row.get("current_speed"),
                "congestion_level": row.get("congestion_level"),
            }
            
            description = (
                f"Traffic congestion on {row.get('street', 'Unknown')} "
                f"({row.get('_direction', '')}) - {row.get('congestion_level', 'unknown')}"
            )
            
            query = text("""
                INSERT INTO hazards_active (
                    type, severity, location, description, source_id, 
                    expires_at, metadata
                )
                VALUES (
                    'TRAFFIC',
                    :severity,
                    ST_GeomFromText(:location, 4326),
                    :description,
                    :source_id,
                    :expires_at,
                    :metadata
                )
                ON CONFLICT (source_id)
                DO UPDATE SET
                    severity = EXCLUDED.severity,
                    description = EXCLUDED.description,
                    expires_at = EXCLUDED.expires_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
            """)
            
            try:
                conn.execute(query, {
                    "severity": int(row.get("severity", 3)),
                    "location": wkt_point,
                    "description": description,
                    "source_id": f"TRAFFIC-{row.get('segmentid')}",
                    "expires_at": expires_at,
                    "metadata": json.dumps(metadata),
                })
                upserted_count += 1
            except Exception as e:
                logger.error(f"Error upserting traffic segment {row.get('segmentid')}: {e}")
        
        conn.commit()
    
    logger.info(f"Successfully upserted {upserted_count} traffic hazards")
    return upserted_count


def cleanup_old_traffic_hazards(engine) -> int:
    """Remove expired traffic hazards."""
    with engine.connect() as conn:
        result = conn.execute(text(
            "DELETE FROM hazards_active WHERE type = 'TRAFFIC' AND expires_at < NOW()"
        ))
        conn.commit()
        return result.rowcount


# ============================================================
# Main Pipeline
# ============================================================

def run_traffic_ingestion(dry_run: bool = False):
    """
    Main entry point for traffic data ingestion.
    
    Implements the School Zone Override: traffic near schools is
    ignored during peak hours (7-9 AM, 2-4 PM).
    """
    logger.info("=" * 60)
    logger.info("AirScout Traffic Ingestion Pipeline")
    logger.info("=" * 60)
    
    now = datetime.now(CHICAGO_TZ)
    logger.info(f"Current time (Chicago): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    if is_school_zone_peak_time():
        logger.info("⚠️  School zone peak hours - traffic near schools will be filtered")
    
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    
    client = Socrata(CHICAGO_DATA_PORTAL, None)
    
    try:
        # Fetch traffic data
        traffic_df = fetch_traffic_data(client)
        
        if traffic_df.empty:
            logger.info("No significant traffic congestion found")
            return
        
        # Get school locations for filtering
        engine = get_engine()
        schools_gdf = fetch_school_locations(engine)
        
        # Apply school zone override
        filtered_traffic = filter_traffic_near_schools(traffic_df, schools_gdf)
        
        if dry_run:
            logger.info(f"\nTraffic hazards that would be created:")
            for _, row in filtered_traffic.head(10).iterrows():
                logger.info(
                    f"  🚗 {row.get('street')} ({row.get('_direction')}) - "
                    f"{row.get('congestion_level')} (severity={row.get('severity')})"
                )
            logger.info(f"\nTotal: {len(filtered_traffic)} traffic hazards")
            return filtered_traffic
        
        # Upsert hazards
        upsert_traffic_hazards(engine, filtered_traffic)
        
        # Cleanup expired
        cleanup_old_traffic_hazards(engine)
        
        logger.info("=" * 60)
        logger.info("Traffic ingestion completed successfully")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AirScout Traffic Ingestion")
    parser.add_argument("--dry-run", action="store_true", help="Run without database writes")
    
    args = parser.parse_args()
    run_traffic_ingestion(dry_run=args.dry_run)

