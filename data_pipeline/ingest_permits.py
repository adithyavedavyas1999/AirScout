"""
AirScout Data Pipeline: Zombie Permit Ingestion Script
======================================================

This script implements the "Zombie Permit" fix from the PRD:
- A demolition permit is ONLY considered a real risk if it's validated
  by a 311 complaint (SVR/NOI codes) within 200 meters in the last 48 hours.

Data Sources:
- Chicago Building Permits: https://data.cityofchicago.org/resource/ydr8-5enu.json
- Chicago 311 Service Requests: https://data.cityofchicago.org/resource/v6vf-nfxy.json

Author: AirScout Team
License: MIT
"""

import os
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Load environment variables from .env file BEFORE other imports
from dotenv import load_dotenv

# Find .env file in project root (parent of data_pipeline directory)
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import pandas as pd
from sodapy import Socrata
from sqlalchemy import create_engine, text
from geoalchemy2 import Geometry, WKTElement
from shapely.geometry import Point

# ============================================================
# Configuration
# ============================================================

# Chicago Data Portal API (no app token required, but rate limited)
CHICAGO_DATA_PORTAL = "data.cityofchicago.org"

# Dataset IDs
PERMITS_DATASET_ID = "ydr8-5enu"  # Building Permits
COMPLAINTS_311_DATASET_ID = "v6vf-nfxy"  # 311 Service Requests (2018-Present)

# Zombie Permit Parameters (from PRD)
COMPLAINT_RADIUS_METERS = 200  # Complaints must be within 200m of permit
COMPLAINT_LOOKBACK_HOURS = 48  # Only consider complaints from last 48 hours

# Hazard expiration (permits expire after 7 days if not re-validated)
HAZARD_EXPIRATION_HOURS = 168  # 7 days

# Permit types to track
DEMOLITION_PERMIT_TYPES = [
    "PERMIT - WRECKING/DEMOLITION",
    "WRECKING/DEMOLITION",
]

# 311 Complaint types that validate permits
VALIDATING_COMPLAINT_TYPES = [
    "SVR",  # Severe Weather/Road condition
    "NOI",  # Noise complaint (dust/equipment noise)
]

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
    """
    Construct Supabase PostgreSQL connection URL from environment variables.
    
    Required env vars:
    - SUPABASE_DB_HOST: Database host (e.g., db.xxxxx.supabase.co)
    - SUPABASE_DB_PORT: Database port (default: 5432)
    - SUPABASE_DB_NAME: Database name (default: postgres)
    - SUPABASE_DB_USER: Database user (default: postgres)
    - SUPABASE_DB_PASSWORD: Database password
    """
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
    database_url = get_database_url()
    return create_engine(database_url, echo=False)


# ============================================================
# Data Fetching Functions
# ============================================================

def fetch_demolition_permits(
    client: Socrata,
    limit: int = 5000
) -> pd.DataFrame:
    """
    Fetch active demolition/wrecking permits from Chicago Data Portal.
    
    Filters:
    - Permit type contains 'WRECKING' or 'DEMOLITION'
    - Issue date within last 365 days (permits can be long-running)
    - Has valid latitude/longitude
    
    Returns:
        DataFrame with columns: permit_number, latitude, longitude, 
        permit_type, work_description, address, issue_date
    """
    logger.info("Fetching demolition permits from Chicago Data Portal...")
    
    # Calculate date filter (permits issued in last year)
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00")
    
    # SoQL query for demolition permits
    # Note: Using $where for complex filtering
    where_clause = (
        f"issue_date >= '{one_year_ago}' "
        f"AND latitude IS NOT NULL "
        f"AND longitude IS NOT NULL "
        f"AND (permit_type LIKE '%WRECKING%' OR permit_type LIKE '%DEMOLITION%')"
    )
    
    try:
        results = client.get(
            PERMITS_DATASET_ID,
            where=where_clause,
            limit=limit,
            select="id, permit_, permit_type, work_description, "
                   "street_number, street_direction, street_name, "
                   "latitude, longitude, issue_date"
        )
        
        if not results:
            logger.warning("No demolition permits found")
            return pd.DataFrame()
        
        df = pd.DataFrame.from_records(results)
        
        # Rename and clean columns
        df = df.rename(columns={
            "permit_": "permit_number",
        })
        
        # Build address string
        df["address"] = (
            df.get("street_number", "").fillna("") + " " +
            df.get("street_direction", "").fillna("") + " " +
            df.get("street_name", "").fillna("")
        ).str.strip()
        
        # Convert types
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df["issue_date"] = pd.to_datetime(df["issue_date"], errors="coerce")
        
        # Drop rows with invalid coordinates
        df = df.dropna(subset=["latitude", "longitude"])
        
        logger.info(f"Fetched {len(df)} demolition permits")
        return df
        
    except Exception as e:
        logger.error(f"Error fetching permits: {e}")
        raise


def fetch_recent_complaints(
    client: Socrata,
    hours_lookback: int = COMPLAINT_LOOKBACK_HOURS,
    limit: int = 10000
) -> pd.DataFrame:
    """
    Fetch recent 311 complaints that can validate demolition permits.
    
    Complaint Types:
    - SVR: Severe Weather/Road condition (includes dust complaints)
    - NOI: Noise complaints (construction/equipment noise)
    
    Args:
        client: Socrata client
        hours_lookback: How far back to look for complaints (default 48 hours)
        limit: Maximum records to fetch
    
    Returns:
        DataFrame with columns: service_request_id, complaint_type,
        latitude, longitude, description, created_date
    """
    logger.info(f"Fetching 311 complaints from last {hours_lookback} hours...")
    
    # Calculate cutoff time
    cutoff_time = (datetime.now() - timedelta(hours=hours_lookback)).strftime("%Y-%m-%dT%H:%M:%S")
    
    # Build complaint type filter
    # Using SR_TYPE field which contains complaint codes
    complaint_types_str = "', '".join(VALIDATING_COMPLAINT_TYPES)
    
    # SoQL query for relevant complaints
    where_clause = (
        f"created_date >= '{cutoff_time}' "
        f"AND latitude IS NOT NULL "
        f"AND longitude IS NOT NULL "
    )
    
    try:
        results = client.get(
            COMPLAINTS_311_DATASET_ID,
            where=where_clause,
            limit=limit,
            select="sr_number, sr_type, sr_short_code, "
                   "latitude, longitude, created_date, status, "
                   "street_address, city, state"
        )
        
        if not results:
            logger.warning("No recent 311 complaints found")
            return pd.DataFrame()
        
        df = pd.DataFrame.from_records(results)
        
        # Rename columns
        df = df.rename(columns={
            "sr_number": "service_request_id",
            "sr_type": "complaint_description",
            "sr_short_code": "complaint_type",
        })
        
        # Filter to only relevant complaint types
        # SVR = Severe weather/road issues (can include dust)
        # NOI = Noise complaints
        # Also include complaints with "DUST" or "DEMOLITION" in description
        dust_keywords = ["DUST", "DEMOLITION", "CONSTRUCTION", "DEBRIS"]
        
        df["is_relevant"] = (
            df["complaint_type"].isin(VALIDATING_COMPLAINT_TYPES) |
            df["complaint_description"].str.upper().str.contains(
                "|".join(dust_keywords), na=False
            )
        )
        df = df[df["is_relevant"]].drop(columns=["is_relevant"])
        
        # Convert types
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
        df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce")
        
        # Drop rows with invalid coordinates
        df = df.dropna(subset=["latitude", "longitude"])
        
        logger.info(f"Fetched {len(df)} relevant 311 complaints")
        return df
        
    except Exception as e:
        logger.error(f"Error fetching complaints: {e}")
        raise


# ============================================================
# Zombie Permit Validation Logic
# ============================================================

def validate_permits_with_complaints(
    permits_df: pd.DataFrame,
    complaints_df: pd.DataFrame,
    radius_meters: float = COMPLAINT_RADIUS_METERS
) -> pd.DataFrame:
    """
    CRITICAL: Implement the "Zombie Permit" fix.
    
    A permit is only considered a real pollution risk if there's
    a validating 311 complaint within 200 meters.
    
    This prevents false positives from permits that:
    - Were issued but work hasn't started
    - Work has completed
    - Permit was cancelled
    
    The presence of a recent complaint indicates ACTIVE work.
    
    Args:
        permits_df: DataFrame of demolition permits
        complaints_df: DataFrame of 311 complaints
        radius_meters: Maximum distance for complaint to validate permit (200m)
    
    Returns:
        DataFrame of validated permits with complaint info
    """
    logger.info(f"Validating permits with {radius_meters}m complaint radius...")
    
    if permits_df.empty or complaints_df.empty:
        logger.warning("No permits or complaints to validate")
        return pd.DataFrame()
    
    # Convert to GeoDataFrames for spatial operations
    import geopandas as gpd
    from shapely.geometry import Point
    
    # Create geometry columns
    permits_geometry = [
        Point(row["longitude"], row["latitude"]) 
        for _, row in permits_df.iterrows()
    ]
    complaints_geometry = [
        Point(row["longitude"], row["latitude"]) 
        for _, row in complaints_df.iterrows()
    ]
    
    # Create GeoDataFrames with WGS84 CRS (EPSG:4326)
    permits_gdf = gpd.GeoDataFrame(
        permits_df,
        geometry=permits_geometry,
        crs="EPSG:4326"
    )
    complaints_gdf = gpd.GeoDataFrame(
        complaints_df,
        geometry=complaints_geometry,
        crs="EPSG:4326"
    )
    
    # Project to a meter-based CRS for accurate distance calculation
    # Using Illinois State Plane East (EPSG:26971) for Chicago
    permits_projected = permits_gdf.to_crs("EPSG:26971")
    complaints_projected = complaints_gdf.to_crs("EPSG:26971")
    
    # Perform spatial join: find permits with complaints within radius
    # Using 'sjoin_nearest' to find closest complaint for each permit
    validated = gpd.sjoin_nearest(
        permits_projected,
        complaints_projected,
        how="inner",
        max_distance=radius_meters,
        distance_col="distance_to_complaint"
    )
    
    # Remove duplicates (keep only closest complaint per permit)
    validated = validated.sort_values("distance_to_complaint")
    validated = validated.drop_duplicates(subset=["permit_number"], keep="first")
    
    # Convert back to WGS84 for storage
    if not validated.empty:
        validated = validated.to_crs("EPSG:4326")
    
    logger.info(f"Validated {len(validated)} permits (out of {len(permits_df)} total)")
    
    return validated


def calculate_severity(
    row: pd.Series,
    complaints_nearby: int
) -> int:
    """
    Calculate hazard severity (1-5) based on:
    - Number of nearby complaints (more = worse)
    - Recency of complaints
    - Permit type
    
    Args:
        row: Permit row
        complaints_nearby: Count of complaints within radius
    
    Returns:
        Severity score 1-5
    """
    base_severity = 3  # Demolition starts at moderate
    
    # Adjust based on complaint count
    if complaints_nearby >= 5:
        base_severity += 2
    elif complaints_nearby >= 2:
        base_severity += 1
    
    # Cap at 5
    return min(base_severity, 5)


# ============================================================
# Database Operations
# ============================================================

def upsert_validated_hazards(
    engine,
    validated_permits: pd.DataFrame
) -> int:
    """
    Insert or update validated permits as active hazards.
    
    Uses PostgreSQL UPSERT (ON CONFLICT) to handle duplicates.
    
    Args:
        engine: SQLAlchemy engine
        validated_permits: DataFrame of validated permits
    
    Returns:
        Number of hazards upserted
    """
    if validated_permits.empty:
        logger.info("No validated permits to upsert")
        return 0
    
    logger.info(f"Upserting {len(validated_permits)} hazards to database...")
    
    # Calculate expiration time
    expires_at = datetime.now() + timedelta(hours=HAZARD_EXPIRATION_HOURS)
    
    upserted_count = 0
    
    with engine.connect() as conn:
        for _, row in validated_permits.iterrows():
            # Extract coordinates from geometry (after spatial join, lon/lat columns may be renamed)
            if 'geometry' in row.index and row['geometry'] is not None:
                lon = row['geometry'].x
                lat = row['geometry'].y
            elif 'longitude_left' in row.index:
                lon = row['longitude_left']
                lat = row['latitude_left']
            elif 'longitude' in row.index:
                lon = row['longitude']
                lat = row['latitude']
            else:
                logger.warning(f"Could not extract coordinates for permit {row.get('permit_number')}")
                continue
            
            # Create WKT for the point geometry
            wkt_point = f"SRID=4326;POINT({lon} {lat})"
            
            # Build metadata JSON
            metadata = {
                "permit_number": row.get("permit_number"),
                "permit_type": row.get("permit_type"),
                "address": row.get("address"),
                "issue_date": str(row.get("issue_date")) if pd.notna(row.get("issue_date")) else None,
                "validating_complaint": row.get("service_request_id"),
                "complaint_type": row.get("complaint_type"),
                "distance_to_complaint_m": round(row.get("distance_to_complaint", 0), 2),
            }
            
            # Calculate severity
            severity = calculate_severity(row, complaints_nearby=1)
            
            # Upsert query
            query = text("""
                INSERT INTO hazards_active (
                    type, severity, location, description, source_id, 
                    expires_at, metadata
                )
                VALUES (
                    'PERMIT', 
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
                    location = EXCLUDED.location,
                    description = EXCLUDED.description,
                    expires_at = EXCLUDED.expires_at,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                WHERE hazards_active.source_id = EXCLUDED.source_id
            """)
            
            try:
                conn.execute(query, {
                    "severity": severity,
                    "location": wkt_point,
                    "description": f"Active demolition at {row.get('address', 'Unknown')}",
                    "source_id": f"PERMIT-{row.get('permit_number')}",
                    "expires_at": expires_at,
                    "metadata": json.dumps(metadata),  # Proper JSON serialization
                })
                upserted_count += 1
            except Exception as e:
                logger.error(f"Error upserting permit {row.get('permit_number')}: {e}")
        
        conn.commit()
    
    logger.info(f"Successfully upserted {upserted_count} hazards")
    return upserted_count


def cleanup_expired_hazards(engine) -> int:
    """Remove hazards past their expiration time."""
    logger.info("Cleaning up expired hazards...")
    
    with engine.connect() as conn:
        result = conn.execute(text(
            "DELETE FROM hazards_active WHERE expires_at < NOW()"
        ))
        conn.commit()
        deleted = result.rowcount
    
    logger.info(f"Deleted {deleted} expired hazards")
    return deleted


# ============================================================
# Main Pipeline
# ============================================================

def run_permit_ingestion(dry_run: bool = False, output_file: Optional[str] = None):
    """
    Main entry point for the Zombie Permit ingestion pipeline.
    
    Steps:
    1. Fetch demolition permits from Chicago Data Portal
    2. Fetch recent 311 complaints (last 48 hours)
    3. Validate permits using spatial join (200m radius)
    4. Upsert validated permits as active hazards (or save to file in dry-run)
    5. Clean up expired hazards
    
    Args:
        dry_run: If True, skip database operations and output results to console/file
        output_file: Optional file path to save validated permits (CSV or JSON)
    """
    logger.info("=" * 60)
    logger.info("Starting AirScout Permit Ingestion Pipeline")
    if dry_run:
        logger.info("*** DRY RUN MODE - No database writes ***")
    logger.info("=" * 60)
    
    # Initialize Socrata client (unauthenticated for free tier)
    # Rate limited to 1000 requests/hour without app token
    client = Socrata(CHICAGO_DATA_PORTAL, None)
    
    try:
        # Step 1: Fetch demolition permits
        permits_df = fetch_demolition_permits(client)
        
        # Step 2: Fetch recent 311 complaints
        complaints_df = fetch_recent_complaints(client)
        
        # Step 3: Validate permits (Zombie Permit logic)
        validated_permits = validate_permits_with_complaints(
            permits_df,
            complaints_df,
            radius_meters=COMPLAINT_RADIUS_METERS
        )
        
        if validated_permits.empty:
            logger.info("No validated permits found - no hazards to create")
            return
        
        # In dry-run mode, output results without database
        if dry_run:
            logger.info("\n" + "=" * 60)
            logger.info("VALIDATED PERMITS (Zombie Permit Logic)")
            logger.info("=" * 60)
            
            # Display key columns
            display_cols = [
                "permit_number", "address", "permit_type",
                "service_request_id", "complaint_type", "distance_to_complaint"
            ]
            available_cols = [c for c in display_cols if c in validated_permits.columns]
            
            for idx, row in validated_permits[available_cols].iterrows():
                logger.info(f"\n📍 Permit: {row.get('permit_number', 'N/A')}")
                logger.info(f"   Address: {row.get('address', 'N/A')}")
                logger.info(f"   Type: {row.get('permit_type', 'N/A')}")
                logger.info(f"   Validating Complaint: {row.get('service_request_id', 'N/A')}")
                logger.info(f"   Complaint Type: {row.get('complaint_type', 'N/A')}")
                logger.info(f"   Distance: {row.get('distance_to_complaint', 0):.1f}m")
            
            # Save to file if specified
            if output_file:
                # Convert geometry to WKT for serialization
                output_df = validated_permits.copy()
                if 'geometry' in output_df.columns:
                    output_df['geometry_wkt'] = output_df['geometry'].apply(
                        lambda g: g.wkt if g else None
                    )
                    output_df = output_df.drop(columns=['geometry'])
                
                if output_file.endswith('.json'):
                    output_df.to_json(output_file, orient='records', indent=2, date_format='iso')
                else:
                    output_df.to_csv(output_file, index=False)
                logger.info(f"\n✅ Results saved to: {output_file}")
            
            logger.info("\n" + "=" * 60)
            logger.info(f"Total: {len(validated_permits)} validated hazards ready for database")
            logger.info("=" * 60)
            return validated_permits
        
        # Step 4: Upsert to database (production mode)
        engine = get_engine()
        upsert_validated_hazards(engine, validated_permits)
        
        # Step 5: Cleanup expired hazards
        cleanup_expired_hazards(engine)
        
        logger.info("=" * 60)
        logger.info("Pipeline completed successfully")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise
    finally:
        client.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="AirScout Zombie Permit Ingestion Pipeline"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true",
        help="Run without database - output validated permits to console"
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Save validated permits to file (CSV or JSON)"
    )
    
    args = parser.parse_args()
    
    run_permit_ingestion(dry_run=args.dry_run, output_file=args.output)


