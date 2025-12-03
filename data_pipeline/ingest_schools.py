"""
AirScout Data Pipeline: School Data Ingestion
==============================================

Fetches Chicago Public Schools locations and populates the schools_static table.
This data is used for the "School Zone Hard Rule" - areas near schools are
automatically marked as HIGH RISK during peak hours (7-9 AM, 2-4 PM).

Data Source:
- Chicago Public Schools: https://data.cityofchicago.org/resource/9xs2-f89t.json

Author: AirScout Team
License: MIT
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Load environment variables from .env file
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import pandas as pd
from sodapy import Socrata
from sqlalchemy import create_engine, text

# ============================================================
# Configuration
# ============================================================

CHICAGO_DATA_PORTAL = "data.cityofchicago.org"
SCHOOLS_DATASET_ID = "9xs2-f89t"  # Chicago Public Schools

# School zone radius (meters) - area around school considered high risk
SCHOOL_ZONE_RADIUS_METERS = 150

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
    """Create SQLAlchemy engine for Supabase PostgreSQL."""
    return create_engine(get_database_url(), echo=False)


# ============================================================
# Data Fetching
# ============================================================

def fetch_chicago_schools(client: Socrata, limit: int = 1000) -> pd.DataFrame:
    """
    Fetch all Chicago Public Schools from the data portal.
    
    Dataset: https://data.cityofchicago.org/resource/9xs2-f89t
    
    Returns:
        DataFrame with columns: school_id, school_name, latitude, longitude,
        address, school_type
    """
    logger.info("Fetching Chicago Public Schools...")
    
    try:
        # Fetch all columns - the API schema varies, so we handle dynamically
        results = client.get(
            SCHOOLS_DATASET_ID,
            limit=limit
        )
        
        if not results:
            logger.warning("No schools found")
            return pd.DataFrame()
        
        df = pd.DataFrame.from_records(results)
        
        # Log available columns for debugging
        logger.info(f"Available columns: {list(df.columns)}")
        
        # Map various possible column names to our standard names
        column_mappings = {
            # School ID options
            "school_id": "school_id",
            "schoolid": "school_id",
            "id": "school_id",
            
            # School name options
            "long_name": "school_name",
            "school_nm": "school_name",
            "name_of_school": "school_name",
            "school_name": "school_name",
            "schoolname": "school_name",
            
            # School type options
            "school_type": "school_type",
            "governance": "school_type",
            "primary_category": "school_type",
            
            # Address options
            "address": "address",
            "street_address": "address",
            "school_address": "address",
        }
        
        # Apply mappings for columns that exist
        rename_dict = {}
        for old_col, new_col in column_mappings.items():
            if old_col in df.columns and new_col not in df.columns:
                rename_dict[old_col] = new_col
        
        if rename_dict:
            df = df.rename(columns=rename_dict)
        
        # Handle coordinates - check for various column names
        lat_cols = ["latitude", "lat", "y", "the_geom"]
        lon_cols = ["longitude", "long", "lng", "x", "the_geom"]
        
        # Try to extract lat/lon
        if "latitude" not in df.columns:
            for col in lat_cols:
                if col in df.columns:
                    if col == "the_geom":
                        # Extract from GeoJSON
                        df["latitude"] = df[col].apply(
                            lambda x: x.get("coordinates", [0, 0])[1] if isinstance(x, dict) else None
                        )
                    else:
                        df["latitude"] = df[col]
                    break
        
        if "longitude" not in df.columns:
            for col in lon_cols:
                if col in df.columns:
                    if col == "the_geom":
                        df["longitude"] = df[col].apply(
                            lambda x: x.get("coordinates", [0, 0])[0] if isinstance(x, dict) else None
                        )
                    else:
                        df["longitude"] = df[col]
                    break
        
        # Build full address from available columns
        address_parts = []
        for col in ["address", "street_address", "city", "state", "zip"]:
            if col in df.columns:
                address_parts.append(df[col].fillna("").astype(str))
        
        if address_parts:
            df["full_address"] = pd.concat(address_parts, axis=1).apply(
                lambda x: ", ".join(filter(None, x)), axis=1
            )
        else:
            df["full_address"] = ""
        
        # Ensure we have school_id
        if "school_id" not in df.columns:
            df["school_id"] = df.index.astype(str)
        
        # Ensure we have school_name
        if "school_name" not in df.columns:
            # Try to find any name-like column
            name_cols = [c for c in df.columns if "name" in c.lower()]
            if name_cols:
                df["school_name"] = df[name_cols[0]]
            else:
                df["school_name"] = "Unknown School"
        
        # Ensure we have school_type
        if "school_type" not in df.columns:
            df["school_type"] = "Public"
        
        # Convert coordinates to numeric
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        
        # Drop rows without valid coordinates
        df = df.dropna(subset=["latitude", "longitude"])
        
        logger.info(f"Fetched {len(df)} schools with valid coordinates")
        return df
        
    except Exception as e:
        logger.error(f"Error fetching schools: {e}")
        raise


# ============================================================
# Database Operations
# ============================================================

def upsert_schools(engine, schools_df: pd.DataFrame) -> int:
    """
    Insert or update schools in the schools_static table.
    
    Args:
        engine: SQLAlchemy engine
        schools_df: DataFrame of schools
    
    Returns:
        Number of schools upserted
    """
    if schools_df.empty:
        logger.info("No schools to upsert")
        return 0
    
    logger.info(f"Upserting {len(schools_df)} schools to database...")
    
    upserted_count = 0
    
    with engine.connect() as conn:
        for _, row in schools_df.iterrows():
            # Create WKT point
            wkt_point = f"SRID=4326;POINT({row['longitude']} {row['latitude']})"
            
            query = text("""
                INSERT INTO schools_static (
                    school_id, school_name, location, address, 
                    zone_radius_meters, school_type, is_active
                )
                VALUES (
                    :school_id,
                    :school_name,
                    ST_GeomFromText(:location, 4326),
                    :address,
                    :zone_radius,
                    :school_type,
                    TRUE
                )
                ON CONFLICT (school_id) 
                DO UPDATE SET
                    school_name = EXCLUDED.school_name,
                    location = EXCLUDED.location,
                    address = EXCLUDED.address,
                    school_type = EXCLUDED.school_type,
                    updated_at = NOW()
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


# ============================================================
# Main Pipeline
# ============================================================

def run_school_ingestion(dry_run: bool = False):
    """
    Main entry point for school data ingestion.
    
    Args:
        dry_run: If True, fetch data but don't write to database
    """
    logger.info("=" * 60)
    logger.info("Starting AirScout School Ingestion Pipeline")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)
    
    client = Socrata(CHICAGO_DATA_PORTAL, None)
    
    try:
        # Fetch schools
        schools_df = fetch_chicago_schools(client)
        
        if dry_run:
            logger.info("\nSample schools fetched:")
            for _, row in schools_df.head(10).iterrows():
                logger.info(f"  🏫 {row.get('school_name')} - {row.get('school_type')}")
            logger.info(f"\nTotal: {len(schools_df)} schools")
            return schools_df
        
        # Upsert to database
        engine = get_engine()
        upsert_schools(engine, schools_df)
        
        logger.info("=" * 60)
        logger.info("School ingestion completed successfully")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AirScout School Ingestion")
    parser.add_argument("--dry-run", action="store_true", help="Run without database writes")
    
    args = parser.parse_args()
    run_school_ingestion(dry_run=args.dry_run)

