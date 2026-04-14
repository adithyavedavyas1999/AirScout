"""
AirScout Data Pipeline: Traffic Congestion Ingestion
=====================================================

Fetches Chicago traffic congestion data and creates TRAFFIC hazards.
Implements School Zone Override: traffic near schools is IGNORED during peak hours.
"""

import json
import logging
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from sodapy import Socrata
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import chicago_data, school_zone

CHICAGO_DATA_PORTAL = chicago_data.base_url
TRAFFIC_DATASET_ID = chicago_data.traffic_dataset
CHICAGO_TZ = ZoneInfo("America/Chicago")

MORNING_START = time(7, 0)
MORNING_END = time(9, 0)
AFTERNOON_START = time(14, 0)
AFTERNOON_END = time(16, 0)
SCHOOL_ZONE_OVERRIDE_RADIUS_METERS = 200

SEVERITY_MAP = {"severe": 5, "heavy": 4, "moderate": 3, "light": 2, "free_flow": 1}
MIN_SEVERITY_THRESHOLD = 3
HAZARD_EXPIRATION_MINUTES = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _socrata_client() -> Socrata:
    token = chicago_data.app_token or None
    return Socrata(CHICAGO_DATA_PORTAL, token)


def is_school_zone_peak_time(dt: datetime = None) -> bool:
    if dt is None:
        dt = datetime.now(CHICAGO_TZ)
    if dt.weekday() > 4:
        return False
    current_time = dt.time()
    return MORNING_START <= current_time < MORNING_END or AFTERNOON_START <= current_time < AFTERNOON_END


def fetch_traffic_data(client: Socrata, limit: int = 2000) -> pd.DataFrame:
    logger.info("Fetching traffic congestion data...")
    try:
        results = client.get(TRAFFIC_DATASET_ID, limit=limit, order="time DESC")
        if not results:
            logger.warning("No traffic data found")
            return pd.DataFrame()

        df = pd.DataFrame.from_records(results)
        logger.info(f"Available columns: {list(df.columns)}")

        column_mappings = {
            "segmentid": "segmentid", "segment_id": "segmentid", "_id": "segmentid",
            "street": "street", "_traffic": "street", "street_name": "street",
            "_direction": "_direction", "direction": "_direction",
            "current_speed": "current_speed", "speed": "current_speed",
            "start_lon": "start_lon", "west": "start_lon", "_lif_lon": "start_lon",
            "start_lat": "start_lat", "south": "start_lat", "_lif_lat": "start_lat",
            "_fromst": "_fromst", "from_street": "_fromst",
            "_tost": "_tost", "to_street": "_tost",
        }

        rename_dict = {}
        for old_col, new_col in column_mappings.items():
            if old_col in df.columns and new_col not in rename_dict.values():
                rename_dict[old_col] = new_col
        if rename_dict:
            df = df.rename(columns=rename_dict)

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

        if "segmentid" not in df.columns:
            df["segmentid"] = df.index.astype(str)
        if "street" not in df.columns:
            street_cols = [c for c in df.columns if "street" in c.lower() or "road" in c.lower()]
            df["street"] = df[street_cols[0]] if street_cols else "Unknown Street"

        for col in ["current_speed", "start_lon", "start_lat", "end_lon", "end_lat"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "start_lat" not in df.columns or "start_lon" not in df.columns:
            logger.error("Could not find coordinate columns in traffic data")
            return pd.DataFrame()

        df = df.dropna(subset=["start_lat", "start_lon"])
        if df.empty:
            return pd.DataFrame()

        ASSUMED_SPEED_LIMIT = 30
        if "current_speed" in df.columns and df["current_speed"].notna().any():
            df["speed_ratio"] = df["current_speed"] / ASSUMED_SPEED_LIMIT

            def classify_congestion(ratio):
                if pd.isna(ratio) or ratio <= 0:
                    return "severe"
                if ratio < 0.25:
                    return "severe"
                if ratio < 0.5:
                    return "heavy"
                if ratio < 0.75:
                    return "moderate"
                if ratio < 0.9:
                    return "light"
                return "free_flow"

            df["congestion_level"] = df["speed_ratio"].apply(classify_congestion)
        else:
            logger.warning("No speed data available")
            df["congestion_level"] = "moderate"
            df["speed_ratio"] = 0.5

        df["severity"] = df["congestion_level"].map(SEVERITY_MAP)
        df = df[df["severity"] >= MIN_SEVERITY_THRESHOLD]

        logger.info(f"Fetched {len(df)} congested traffic segments")
        return df
    except Exception as e:
        logger.error(f"Error fetching traffic data: {e}")
        raise


def fetch_school_locations(engine) -> gpd.GeoDataFrame:
    query = "SELECT school_id, school_name, ST_X(location::geometry) as lon, ST_Y(location::geometry) as lat, zone_radius_meters FROM schools_static WHERE is_active = TRUE"
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    if df.empty:
        return gpd.GeoDataFrame()
    geometry = [Point(row["lon"], row["lat"]) for _, row in df.iterrows()]
    return gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")


def filter_traffic_near_schools(traffic_df: pd.DataFrame, schools_gdf: gpd.GeoDataFrame, radius_meters: float = SCHOOL_ZONE_OVERRIDE_RADIUS_METERS) -> pd.DataFrame:
    if not is_school_zone_peak_time():
        logger.info("Not in school peak hours - no filtering applied")
        return traffic_df
    if traffic_df.empty or schools_gdf.empty:
        return traffic_df

    logger.info(f"School zone override active - filtering traffic within {radius_meters}m of schools")

    traffic_geometry = [Point(row["start_lon"], row["start_lat"]) for _, row in traffic_df.iterrows()]
    traffic_gdf = gpd.GeoDataFrame(traffic_df, geometry=traffic_geometry, crs="EPSG:4326")

    traffic_projected = traffic_gdf.to_crs("EPSG:26971")
    schools_projected = schools_gdf.to_crs("EPSG:26971")
    schools_projected["buffer"] = schools_projected.geometry.buffer(radius_meters)
    school_zones = schools_projected.set_geometry("buffer").unary_union
    traffic_projected["near_school"] = traffic_projected.geometry.within(school_zones)
    filtered = traffic_projected[~traffic_projected["near_school"]]
    removed = len(traffic_projected) - len(filtered)
    logger.info(f"Removed {removed} traffic segments near schools")
    return pd.DataFrame(filtered.drop(columns=["geometry", "near_school"]))


def upsert_traffic_hazards(engine, traffic_df: pd.DataFrame) -> int:
    if traffic_df.empty:
        return 0

    logger.info(f"Upserting {len(traffic_df)} traffic hazards...")
    expires_at = datetime.now(CHICAGO_TZ) + timedelta(minutes=HAZARD_EXPIRATION_MINUTES)
    upserted_count = 0

    with engine.connect() as conn:
        for _, row in traffic_df.iterrows():
            wkt_point = f"SRID=4326;POINT({row['start_lon']} {row['start_lat']})"
            metadata = {"segment_id": row.get("segmentid"), "street": row.get("street"), "direction": row.get("_direction"), "current_speed": row.get("current_speed"), "congestion_level": row.get("congestion_level")}
            description = f"Traffic congestion on {row.get('street', 'Unknown')} ({row.get('_direction', '')}) - {row.get('congestion_level', 'unknown')}"

            query = text("""
                INSERT INTO hazards_active (type, severity, location, description, source_id, expires_at, metadata)
                VALUES ('TRAFFIC', :severity, ST_GeomFromText(:location, 4326), :description, :source_id, :expires_at, :metadata)
                ON CONFLICT (source_id) DO UPDATE SET severity = EXCLUDED.severity, description = EXCLUDED.description, expires_at = EXCLUDED.expires_at, metadata = EXCLUDED.metadata, updated_at = NOW()
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
    with engine.connect() as conn:
        result = conn.execute(text("DELETE FROM hazards_active WHERE type = 'TRAFFIC' AND expires_at < NOW()"))
        conn.commit()
        return result.rowcount


def run_traffic_ingestion(dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("AirScout Traffic Ingestion Pipeline")
    logger.info("=" * 60)

    now = datetime.now(CHICAGO_TZ)
    logger.info(f"Current time (Chicago): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if is_school_zone_peak_time():
        logger.info("School zone peak hours - traffic near schools will be filtered")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")

    client = _socrata_client()
    try:
        traffic_df = fetch_traffic_data(client)
        if traffic_df.empty:
            logger.info("No significant traffic congestion found")
            return

        engine = get_engine()
        schools_gdf = fetch_school_locations(engine)
        filtered_traffic = filter_traffic_near_schools(traffic_df, schools_gdf)

        if dry_run:
            for _, row in filtered_traffic.head(10).iterrows():
                logger.info(f"  {row.get('street')} ({row.get('_direction')}) - {row.get('congestion_level')} (severity={row.get('severity')})")
            logger.info(f"Total: {len(filtered_traffic)} traffic hazards")
            return filtered_traffic

        upsert_traffic_hazards(engine, filtered_traffic)
        cleanup_old_traffic_hazards(engine)
        logger.info("Traffic ingestion completed successfully")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AirScout Traffic Ingestion")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_traffic_ingestion(dry_run=args.dry_run)
