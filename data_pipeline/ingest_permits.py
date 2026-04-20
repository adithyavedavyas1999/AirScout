"""
AirScout Data Pipeline: Zombie Permit Ingestion
================================================

A demolition permit is ONLY considered a real risk if validated
by a 311 complaint (SVR/NOI) within 200m in the last 48 hours.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import geopandas as gpd
from requests.exceptions import HTTPError, ConnectionError, Timeout
from shapely.geometry import Point
from sodapy import Socrata
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import chicago_data, zombie_permit

CHICAGO_DATA_PORTAL = chicago_data.base_url
PERMITS_DATASET_ID = chicago_data.permits_dataset
COMPLAINTS_311_DATASET_ID = chicago_data.complaints_311_dataset

COMPLAINT_RADIUS_METERS = zombie_permit.complaint_radius_meters
COMPLAINT_LOOKBACK_HOURS = zombie_permit.complaint_lookback_hours
HAZARD_EXPIRATION_HOURS = zombie_permit.hazard_expiration_hours
DEMOLITION_PERMIT_TYPES = zombie_permit.permit_types
VALIDATING_COMPLAINT_TYPES = zombie_permit.validating_complaint_types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 10
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _fetch_with_retry(client: Socrata, dataset_id: str, **kwargs) -> list:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.get(dataset_id, **kwargs)
        except (HTTPError, ConnectionError, Timeout) as e:
            last_error = e
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status and status not in RETRYABLE_STATUS_CODES:
                raise
            wait = RETRY_BACKOFF_SECONDS * attempt
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed ({e}), retrying in {wait}s...")
            time.sleep(wait)
    logger.error(f"All {MAX_RETRIES} attempts failed: {last_error}")
    return []


def _socrata_client() -> Socrata:
    token = chicago_data.app_token or None
    return Socrata(CHICAGO_DATA_PORTAL, token)


def fetch_demolition_permits(client: Socrata, limit: int = 5000) -> pd.DataFrame:
    logger.info("Fetching demolition permits from Chicago Data Portal...")
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00")

    where_clause = (
        f"issue_date >= '{one_year_ago}' "
        f"AND latitude IS NOT NULL AND longitude IS NOT NULL "
        f"AND (permit_type LIKE '%WRECKING%' OR permit_type LIKE '%DEMOLITION%')"
    )

    results = _fetch_with_retry(
        client, PERMITS_DATASET_ID,
        where=where_clause,
        limit=limit,
        select="id, permit_, permit_type, work_description, "
               "street_number, street_direction, street_name, "
               "latitude, longitude, issue_date",
    )
    if not results:
        logger.warning("No demolition permits found")
        return pd.DataFrame()

    df = pd.DataFrame.from_records(results)
    df = df.rename(columns={"permit_": "permit_number"})

    df["address"] = (
        df.get("street_number", pd.Series(dtype=str)).fillna("") + " " +
        df.get("street_direction", pd.Series(dtype=str)).fillna("") + " " +
        df.get("street_name", pd.Series(dtype=str)).fillna("")
    ).str.strip()

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["issue_date"] = pd.to_datetime(df["issue_date"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])

    logger.info(f"Fetched {len(df)} demolition permits")
    return df


def fetch_recent_complaints(client: Socrata, hours_lookback: int = COMPLAINT_LOOKBACK_HOURS, limit: int = 10000) -> pd.DataFrame:
    logger.info(f"Fetching 311 complaints from last {hours_lookback} hours...")
    cutoff_time = (datetime.now() - timedelta(hours=hours_lookback)).strftime("%Y-%m-%dT%H:%M:%S")

    where_clause = f"created_date >= '{cutoff_time}' AND latitude IS NOT NULL AND longitude IS NOT NULL"

    results = _fetch_with_retry(
        client, COMPLAINTS_311_DATASET_ID,
        where=where_clause,
        limit=limit,
        select="sr_number, sr_type, sr_short_code, latitude, longitude, created_date, status, street_address, city, state",
    )
    if not results:
        logger.warning("No recent 311 complaints found")
        return pd.DataFrame()

    df = pd.DataFrame.from_records(results)
    df = df.rename(columns={"sr_number": "service_request_id", "sr_type": "complaint_description", "sr_short_code": "complaint_type"})

    dust_keywords = ["DUST", "DEMOLITION", "CONSTRUCTION", "DEBRIS"]
    df["is_relevant"] = (
        df["complaint_type"].isin(VALIDATING_COMPLAINT_TYPES)
        | df["complaint_description"].str.upper().str.contains("|".join(dust_keywords), na=False)
    )
    df = df[df["is_relevant"]].drop(columns=["is_relevant"])

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])

    logger.info(f"Fetched {len(df)} relevant 311 complaints")
    return df


def validate_permits_with_complaints(permits_df: pd.DataFrame, complaints_df: pd.DataFrame, radius_meters: float = COMPLAINT_RADIUS_METERS) -> pd.DataFrame:
    logger.info(f"Validating permits with {radius_meters}m complaint radius...")
    if permits_df.empty or complaints_df.empty:
        logger.warning("No permits or complaints to validate")
        return pd.DataFrame()

    permits_geometry = [Point(row["longitude"], row["latitude"]) for _, row in permits_df.iterrows()]
    complaints_geometry = [Point(row["longitude"], row["latitude"]) for _, row in complaints_df.iterrows()]

    permits_gdf = gpd.GeoDataFrame(permits_df, geometry=permits_geometry, crs="EPSG:4326")
    complaints_gdf = gpd.GeoDataFrame(complaints_df, geometry=complaints_geometry, crs="EPSG:4326")

    permits_projected = permits_gdf.to_crs("EPSG:26971")
    complaints_projected = complaints_gdf.to_crs("EPSG:26971")

    validated = gpd.sjoin_nearest(
        permits_projected, complaints_projected, how="inner",
        max_distance=radius_meters, distance_col="distance_to_complaint",
    )
    validated = validated.sort_values("distance_to_complaint").drop_duplicates(subset=["permit_number"], keep="first")

    if not validated.empty:
        validated = validated.to_crs("EPSG:4326")

    logger.info(f"Validated {len(validated)} permits (out of {len(permits_df)} total)")
    return validated


def calculate_severity(row: pd.Series, complaints_nearby: int) -> int:
    base_severity = 3
    if complaints_nearby >= 5:
        base_severity += 2
    elif complaints_nearby >= 2:
        base_severity += 1
    return min(base_severity, 5)


def upsert_validated_hazards(engine, validated_permits: pd.DataFrame) -> int:
    if validated_permits.empty:
        return 0

    logger.info(f"Upserting {len(validated_permits)} hazards to database...")
    expires_at = datetime.now() + timedelta(hours=HAZARD_EXPIRATION_HOURS)
    upserted_count = 0

    with engine.connect() as conn:
        for _, row in validated_permits.iterrows():
            if "geometry" in row.index and row["geometry"] is not None:
                lon, lat = row["geometry"].x, row["geometry"].y
            elif "longitude_left" in row.index:
                lon, lat = row["longitude_left"], row["latitude_left"]
            elif "longitude" in row.index:
                lon, lat = row["longitude"], row["latitude"]
            else:
                continue

            wkt_point = f"SRID=4326;POINT({lon} {lat})"
            metadata = {
                "permit_number": row.get("permit_number"),
                "permit_type": row.get("permit_type"),
                "address": row.get("address"),
                "issue_date": str(row.get("issue_date")) if pd.notna(row.get("issue_date")) else None,
                "validating_complaint": row.get("service_request_id"),
                "complaint_type": row.get("complaint_type"),
                "distance_to_complaint_m": round(row.get("distance_to_complaint", 0), 2),
            }

            query = text("""
                INSERT INTO hazards_active (type, severity, location, description, source_id, expires_at, metadata)
                VALUES ('PERMIT', :severity, ST_GeomFromText(:location, 4326), :description, :source_id, :expires_at, :metadata)
                ON CONFLICT (source_id)
                DO UPDATE SET severity = EXCLUDED.severity, location = EXCLUDED.location,
                    description = EXCLUDED.description, expires_at = EXCLUDED.expires_at,
                    metadata = EXCLUDED.metadata, updated_at = NOW()
                WHERE hazards_active.source_id = EXCLUDED.source_id
            """)

            try:
                conn.execute(query, {
                    "severity": calculate_severity(row, complaints_nearby=1),
                    "location": wkt_point,
                    "description": f"Active demolition at {row.get('address', 'Unknown')}",
                    "source_id": f"PERMIT-{row.get('permit_number')}",
                    "expires_at": expires_at,
                    "metadata": json.dumps(metadata),
                })
                upserted_count += 1
            except Exception as e:
                logger.error(f"Error upserting permit {row.get('permit_number')}: {e}")

        conn.commit()

    logger.info(f"Successfully upserted {upserted_count} hazards")
    return upserted_count


def cleanup_expired_hazards(engine) -> int:
    with engine.connect() as conn:
        result = conn.execute(text("DELETE FROM hazards_active WHERE expires_at < NOW()"))
        conn.commit()
        deleted = result.rowcount
    logger.info(f"Deleted {deleted} expired hazards")
    return deleted


def run_permit_ingestion(dry_run: bool = False, output_file: Optional[str] = None):
    logger.info("=" * 60)
    logger.info("Starting AirScout Permit Ingestion Pipeline")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)

    client = _socrata_client()
    try:
        permits_df = fetch_demolition_permits(client)
        complaints_df = fetch_recent_complaints(client)
        validated_permits = validate_permits_with_complaints(permits_df, complaints_df, radius_meters=COMPLAINT_RADIUS_METERS)

        if validated_permits.empty:
            logger.info("No validated permits found")
            return

        if dry_run:
            display_cols = [c for c in ["permit_number", "address", "permit_type", "service_request_id", "complaint_type", "distance_to_complaint"] if c in validated_permits.columns]
            for _, row in validated_permits[display_cols].iterrows():
                logger.info(f"  Permit: {row.get('permit_number', 'N/A')} | {row.get('address', 'N/A')}")

            if output_file:
                output_df = validated_permits.copy()
                if "geometry" in output_df.columns:
                    output_df["geometry_wkt"] = output_df["geometry"].apply(lambda g: g.wkt if g else None)
                    output_df = output_df.drop(columns=["geometry"])
                if output_file.endswith(".json"):
                    output_df.to_json(output_file, orient="records", indent=2, date_format="iso")
                else:
                    output_df.to_csv(output_file, index=False)
            return validated_permits

        engine = get_engine()
        upsert_validated_hazards(engine, validated_permits)
        cleanup_expired_hazards(engine)

        logger.info("Pipeline completed successfully")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AirScout Zombie Permit Ingestion Pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", "-o", type=str)
    args = parser.parse_args()
    run_permit_ingestion(dry_run=args.dry_run, output_file=args.output)
