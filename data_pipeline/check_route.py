"""
AirScout Route Hazard Checker
=============================

Implements the "Geospatial Buffer" fix from the PRD:
- Creates a 25-meter polygon buffer around user routes
- Catches hazards on adjacent blocks, not just exact intersections

This module can be:
1. Run as CLI to check a route
2. Imported for use in the dashboard/API
3. Called from Supabase Edge Functions

Author: AirScout Team
License: MIT
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

# Load environment variables
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(env_path)

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely import wkt
from sqlalchemy import create_engine, text

# ============================================================
# Configuration
# ============================================================

CHICAGO_TZ = ZoneInfo("America/Chicago")

# Geospatial Buffer Parameters (from PRD)
ROUTE_BUFFER_METERS = 25  # Buffer around user routes to catch adjacent hazards

# CRS for projections
WGS84 = "EPSG:4326"  # Standard GPS coordinates
ILLINOIS_STATE_PLANE = "EPSG:26971"  # Meters-based projection for Chicago

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
# Route Parsing
# ============================================================

def parse_route_coordinates(coordinates: List[List[float]]) -> LineString:
    """
    Parse route coordinates into a Shapely LineString.
    
    Args:
        coordinates: List of [longitude, latitude] pairs
                    e.g., [[-87.6298, 41.8781], [-87.6350, 41.8820]]
    
    Returns:
        Shapely LineString geometry
    """
    if len(coordinates) < 2:
        raise ValueError("Route must have at least 2 coordinate pairs")
    
    return LineString(coordinates)


def parse_route_wkt(wkt_string: str) -> LineString:
    """
    Parse a WKT LINESTRING into a Shapely geometry.
    
    Args:
        wkt_string: WKT format string
                   e.g., "LINESTRING(-87.6298 41.8781, -87.6350 41.8820)"
    
    Returns:
        Shapely LineString geometry
    """
    return wkt.loads(wkt_string)


# ============================================================
# Geospatial Buffer Logic
# ============================================================

def create_route_buffer(
    route: LineString,
    buffer_meters: float = ROUTE_BUFFER_METERS
) -> gpd.GeoDataFrame:
    """
    Create a buffer polygon around a route.
    
    This implements the PRD requirement:
    "Create a 25-meter polygon buffer around user routes to catch
    hazards on adjacent blocks."
    
    Args:
        route: Shapely LineString representing the route
        buffer_meters: Buffer distance in meters (default 25m)
    
    Returns:
        GeoDataFrame with the buffered route polygon
    """
    # Create GeoDataFrame with WGS84 CRS
    route_gdf = gpd.GeoDataFrame(
        {"geometry": [route]},
        crs=WGS84
    )
    
    # Project to meters-based CRS for accurate buffering
    route_projected = route_gdf.to_crs(ILLINOIS_STATE_PLANE)
    
    # Create buffer
    route_projected["geometry"] = route_projected.geometry.buffer(buffer_meters)
    
    # Convert back to WGS84
    buffered = route_projected.to_crs(WGS84)
    
    return buffered


def check_hazards_along_route(
    engine,
    route: LineString,
    buffer_meters: float = ROUTE_BUFFER_METERS,
    min_severity: int = 1
) -> List[Dict]:
    """
    Check for active hazards within the buffer zone of a route.
    
    Args:
        engine: SQLAlchemy engine
        route: Shapely LineString representing the route
        buffer_meters: Buffer distance in meters
        min_severity: Minimum severity level to return
    
    Returns:
        List of hazard dictionaries with distance to route
    """
    logger.info(f"Checking hazards along route (buffer={buffer_meters}m)...")
    
    # Create buffered route
    buffered_route = create_route_buffer(route, buffer_meters)
    buffer_wkt = buffered_route.geometry.iloc[0].wkt
    
    # Query hazards within buffer using PostGIS
    query = text("""
        SELECT 
            id, type, severity, description, source_id,
            ST_X(location::geometry) as longitude,
            ST_Y(location::geometry) as latitude,
            expires_at, metadata,
            ST_Distance(
                location::geography,
                ST_GeomFromText(:route_wkt, 4326)::geography
            ) as distance_meters
        FROM hazards_active
        WHERE expires_at > NOW()
          AND severity >= :min_severity
          AND ST_Intersects(
              location,
              ST_GeomFromText(:buffer_wkt, 4326)
          )
        ORDER BY distance_meters ASC, severity DESC
    """)
    
    with engine.connect() as conn:
        result = conn.execute(query, {
            "route_wkt": route.wkt,
            "buffer_wkt": buffer_wkt,
            "min_severity": min_severity
        })
        
        hazards = []
        for row in result:
            hazards.append({
                "id": str(row.id),
                "type": row.type,
                "severity": row.severity,
                "description": row.description,
                "source_id": row.source_id,
                "longitude": float(row.longitude),
                "latitude": float(row.latitude),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "distance_meters": round(row.distance_meters, 1),
                "metadata": row.metadata if isinstance(row.metadata, dict) else {}
            })
    
    logger.info(f"Found {len(hazards)} hazards within {buffer_meters}m of route")
    return hazards


def calculate_route_risk_score(hazards: List[Dict]) -> Dict:
    """
    Calculate an overall risk score for a route based on hazards.
    
    Args:
        hazards: List of hazard dictionaries
    
    Returns:
        Risk assessment dictionary
    """
    if not hazards:
        return {
            "score": 0,
            "level": "LOW",
            "message": "No hazards detected along this route",
            "hazard_count": 0
        }
    
    # Calculate weighted score (closer + more severe = higher risk)
    total_score = 0
    for h in hazards:
        # Distance weight: closer = more impactful
        distance_weight = max(0, 1 - (h["distance_meters"] / ROUTE_BUFFER_METERS))
        
        # Severity is 1-5, normalize to 0.2-1.0
        severity_weight = h["severity"] / 5
        
        total_score += distance_weight * severity_weight * 20
    
    # Normalize to 0-100
    score = min(100, int(total_score))
    
    # Determine level
    if score >= 70:
        level = "HIGH"
        message = "High pollution risk - consider alternate route"
    elif score >= 40:
        level = "MODERATE"
        message = "Moderate pollution risk - be aware of hazards"
    else:
        level = "LOW"
        message = "Low pollution risk - route is relatively clear"
    
    return {
        "score": score,
        "level": level,
        "message": message,
        "hazard_count": len(hazards),
        "highest_severity": max(h["severity"] for h in hazards)
    }


# ============================================================
# Main API Functions
# ============================================================

def check_route(
    coordinates: List[List[float]] = None,
    wkt_string: str = None,
    buffer_meters: float = ROUTE_BUFFER_METERS,
    min_severity: int = 1
) -> Dict:
    """
    Main API function to check a route for hazards.
    
    Args:
        coordinates: List of [longitude, latitude] pairs
        wkt_string: Alternative WKT format route
        buffer_meters: Buffer distance (default 25m)
        min_severity: Minimum severity to report
    
    Returns:
        Complete route assessment with hazards and risk score
    """
    # Parse route
    if coordinates:
        route = parse_route_coordinates(coordinates)
    elif wkt_string:
        route = parse_route_wkt(wkt_string)
    else:
        raise ValueError("Must provide either coordinates or wkt_string")
    
    # Get hazards
    engine = get_engine()
    hazards = check_hazards_along_route(
        engine, route, buffer_meters, min_severity
    )
    
    # Calculate risk
    risk = calculate_route_risk_score(hazards)
    
    return {
        "checked_at": datetime.now(CHICAGO_TZ).isoformat(),
        "buffer_meters": buffer_meters,
        "route_length_km": round(route.length * 111, 2),  # Rough conversion
        "risk_assessment": risk,
        "hazards": hazards
    }


# ============================================================
# CLI Interface
# ============================================================

def main():
    """CLI interface for route checking."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="AirScout Route Hazard Checker"
    )
    parser.add_argument(
        "--coords",
        type=str,
        help="Route coordinates as JSON: '[[-87.63,41.88],[-87.64,41.89]]'"
    )
    parser.add_argument(
        "--wkt",
        type=str,
        help="Route as WKT LINESTRING"
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=ROUTE_BUFFER_METERS,
        help=f"Buffer distance in meters (default: {ROUTE_BUFFER_METERS})"
    )
    parser.add_argument(
        "--min-severity",
        type=int,
        default=1,
        help="Minimum severity level (1-5, default: 1)"
    )
    
    args = parser.parse_args()
    
    # Parse coordinates if provided
    coordinates = None
    if args.coords:
        coordinates = json.loads(args.coords)
    
    if not coordinates and not args.wkt:
        # Demo route: Downtown Chicago to Lincoln Park
        logger.info("No route provided - using demo route")
        coordinates = [
            [-87.6298, 41.8781],  # Downtown
            [-87.6350, 41.8850],
            [-87.6400, 41.9000],
            [-87.6450, 41.9150],  # Lincoln Park
        ]
    
    try:
        result = check_route(
            coordinates=coordinates,
            wkt_string=args.wkt,
            buffer_meters=args.buffer,
            min_severity=args.min_severity
        )
        
        # Pretty print results
        print("\n" + "=" * 60)
        print("🌬️  AIRSCOUT ROUTE CHECK")
        print("=" * 60)
        
        risk = result["risk_assessment"]
        print(f"\n📊 Risk Level: {risk['level']} (Score: {risk['score']}/100)")
        print(f"   {risk['message']}")
        print(f"   Hazards found: {risk['hazard_count']}")
        
        if result["hazards"]:
            print("\n⚠️  HAZARDS ALONG ROUTE:")
            for h in result["hazards"][:10]:  # Show top 10
                icon = {"PERMIT": "🏗️", "TRAFFIC": "🚗", "SCHOOL": "🏫"}.get(h["type"], "⚠️")
                print(f"\n   {icon} {h['type']} (Severity {h['severity']}/5)")
                print(f"      {h['description'][:60]}...")
                print(f"      Distance: {h['distance_meters']}m from route")
        
        print("\n" + "=" * 60)
        
    except Exception as e:
        logger.error(f"Route check failed: {e}")
        raise


if __name__ == "__main__":
    main()

