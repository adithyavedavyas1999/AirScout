"""
AirScout Data Pipeline: School Data Ingestion
==============================================

Fetches Chicago Public Schools locations for the School Zone Hard Rule.
"""

import logging
from typing import Optional

import pandas as pd
from sodapy import Socrata
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.config import chicago_data, school_zone

CHICAGO_DATA_PORTAL = chicago_data.base_url
SCHOOLS_DATASET_ID = chicago_data.schools_dataset
SCHOOL_ZONE_RADIUS_METERS = school_zone.zone_radius_meters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _socrata_client() -> Socrata:
    token = chicago_data.app_token or None
    return Socrata(CHICAGO_DATA_PORTAL, token)


def fetch_chicago_schools(client: Socrata, limit: int = 1000) -> pd.DataFrame:
    logger.info("Fetching Chicago Public Schools...")
    try:
        results = client.get(SCHOOLS_DATASET_ID, limit=limit)
        if not results:
            logger.warning("No schools found")
            return pd.DataFrame()

        df = pd.DataFrame.from_records(results)
        logger.info(f"Available columns: {list(df.columns)}")

        column_mappings = {
            "school_id": "school_id", "schoolid": "school_id", "id": "school_id",
            "long_name": "school_name", "school_nm": "school_name", "name_of_school": "school_name",
            "school_name": "school_name", "schoolname": "school_name",
            "school_type": "school_type", "governance": "school_type", "primary_category": "school_type",
            "address": "address", "street_address": "address", "school_address": "address",
        }

        rename_dict = {}
        for old_col, new_col in column_mappings.items():
            if old_col in df.columns and new_col not in df.columns:
                rename_dict[old_col] = new_col
        if rename_dict:
            df = df.rename(columns=rename_dict)

        lat_cols = ["latitude", "lat", "y", "the_geom"]
        lon_cols = ["longitude", "long", "lng", "x", "the_geom"]

        if "latitude" not in df.columns:
            for col in lat_cols:
                if col in df.columns:
                    if col == "the_geom":
                        df["latitude"] = df[col].apply(lambda x: x.get("coordinates", [0, 0])[1] if isinstance(x, dict) else None)
                    else:
                        df["latitude"] = df[col]
                    break

        if "longitude" not in df.columns:
            for col in lon_cols:
                if col in df.columns:
                    if col == "the_geom":
                        df["longitude"] = df[col].apply(lambda x: x.get("coordinates", [0, 0])[0] if isinstance(x, dict) else None)
                    else:
                        df["longitude"] = df[col]
                    break

        address_parts = [df[col].fillna("").astype(str) for col in ["address", "street_address", "city", "state", "zip"] if col in df.columns]
        df["full_address"] = pd.concat(address_parts, axis=1).apply(lambda x: ", ".join(filter(None, x)), axis=1) if address_parts else ""

        if "school_id" not in df.columns:
            df["school_id"] = df.index.astype(str)
        if "school_name" not in df.columns:
            name_cols = [c for c in df.columns if "name" in c.lower()]
            df["school_name"] = df[name_cols[0]] if name_cols else "Unknown School"
        if "school_type" not in df.columns:
            df["school_type"] = "Public"

        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df = df.dropna(subset=["latitude", "longitude"])

        logger.info(f"Fetched {len(df)} schools with valid coordinates")
        return df
    except Exception as e:
        logger.error(f"Error fetching schools: {e}")
        raise


def upsert_schools(engine, schools_df: pd.DataFrame) -> int:
    if schools_df.empty:
        return 0

    logger.info(f"Upserting {len(schools_df)} schools to database...")
    upserted_count = 0

    with engine.connect() as conn:
        for _, row in schools_df.iterrows():
            wkt_point = f"SRID=4326;POINT({row['longitude']} {row['latitude']})"
            query = text("""
                INSERT INTO schools_static (school_id, school_name, location, address, zone_radius_meters, school_type, is_active)
                VALUES (:school_id, :school_name, ST_GeomFromText(:location, 4326), :address, :zone_radius, :school_type, TRUE)
                ON CONFLICT (school_id)
                DO UPDATE SET school_name = EXCLUDED.school_name, location = EXCLUDED.location,
                    address = EXCLUDED.address, school_type = EXCLUDED.school_type, updated_at = NOW()
            """)
            try:
                conn.execute(query, {
                    "school_id": str(row.get("school_id", "")),
                    "school_name": row.get("school_name", "Unknown School"),
                    "location": wkt_point,
                    "address": row.get("full_address", ""),
                    "zone_radius": SCHOOL_ZONE_RADIUS_METERS,
                    "school_type": row.get("school_type", ""),
                })
                upserted_count += 1
            except Exception as e:
                logger.error(f"Error upserting school {row.get('school_id')}: {e}")
        conn.commit()

    logger.info(f"Successfully upserted {upserted_count} schools")
    return upserted_count


def run_school_ingestion(dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("Starting AirScout School Ingestion Pipeline")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)

    client = _socrata_client()
    try:
        schools_df = fetch_chicago_schools(client)
        if dry_run:
            for _, row in schools_df.head(10).iterrows():
                logger.info(f"  {row.get('school_name')} - {row.get('school_type')}")
            logger.info(f"Total: {len(schools_df)} schools")
            return schools_df

        engine = get_engine()
        upsert_schools(engine, schools_df)
        logger.info("School ingestion completed successfully")
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AirScout School Ingestion")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_school_ingestion(dry_run=args.dry_run)
