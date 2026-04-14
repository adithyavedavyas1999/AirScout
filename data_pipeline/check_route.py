"""
AirScout Route Hazard Checker
=============================

Implements the 25-meter polygon buffer around user routes to catch
hazards on adjacent blocks. Supports OSRM-based hazard-aware routing.
"""

import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
from shapely import wkt
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.scoring import (
    calculate_risk_score,
    risk_message,
    ROUTE_BUFFER_METERS,
)
from data_pipeline.config import routing as routing_config

CHICAGO_TZ = ZoneInfo("America/Chicago")
WGS84 = "EPSG:4326"
ILLINOIS_STATE_PLANE = "EPSG:26971"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Route Parsing
# ------------------------------------------------------------------

def parse_route_coordinates(coordinates: List[List[float]]) -> LineString:
    if len(coordinates) < 2:
        raise ValueError("Route must have at least 2 coordinate pairs")
    return LineString(coordinates)


def parse_route_wkt(wkt_string: str) -> LineString:
    return wkt.loads(wkt_string)


# ------------------------------------------------------------------
# Geospatial Buffer
# ------------------------------------------------------------------

def create_route_buffer(route: LineString, buffer_meters: float = ROUTE_BUFFER_METERS) -> gpd.GeoDataFrame:
    route_gdf = gpd.GeoDataFrame({"geometry": [route]}, crs=WGS84)
    route_projected = route_gdf.to_crs(ILLINOIS_STATE_PLANE)
    route_projected["geometry"] = route_projected.geometry.buffer(buffer_meters)
    return route_projected.to_crs(WGS84)


def calculate_route_length_km(route: LineString) -> float:
    """Accurate route length using Illinois State Plane projection."""
    gdf = gpd.GeoDataFrame({"geometry": [route]}, crs=WGS84)
    projected = gdf.to_crs(ILLINOIS_STATE_PLANE)
    return round(projected.geometry.length.iloc[0] / 1000, 2)


def check_hazards_along_route(
    engine,
    route: LineString,
    buffer_meters: float = ROUTE_BUFFER_METERS,
    min_severity: int = 1,
) -> List[Dict]:
    buffered_route = create_route_buffer(route, buffer_meters)
    buffer_wkt = buffered_route.geometry.iloc[0].wkt

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
          AND ST_Intersects(location, ST_GeomFromText(:buffer_wkt, 4326))
        ORDER BY distance_meters ASC, severity DESC
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {
            "route_wkt": route.wkt,
            "buffer_wkt": buffer_wkt,
            "min_severity": min_severity,
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
                "metadata": row.metadata if isinstance(row.metadata, dict) else {},
            })

    logger.info(f"Found {len(hazards)} hazards within {buffer_meters}m of route")
    return hazards


# ------------------------------------------------------------------
# OSRM Routing
# ------------------------------------------------------------------

def get_osrm_route(start: List[float], end: List[float], alternatives: int = 3) -> List[Dict]:
    """
    Fetch walking routes from OSRM.

    Args:
        start: [longitude, latitude]
        end: [longitude, latitude]
        alternatives: number of alternative routes

    Returns:
        List of route dicts with 'coordinates', 'distance_m', 'duration_s'
    """
    import requests

    base = routing_config.osrm_base_url
    profile = routing_config.profile
    coords = f"{start[0]},{start[1]};{end[0]},{end[1]}"
    url = f"{base}/route/v1/{profile}/{coords}"

    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "true" if alternatives > 1 else "false",
        "steps": "false",
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "Ok":
            logger.warning(f"OSRM returned: {data.get('code')}")
            return []

        routes = []
        for r in data.get("routes", [])[:alternatives]:
            coords_list = r["geometry"]["coordinates"]
            routes.append({
                "coordinates": coords_list,
                "distance_m": r["distance"],
                "duration_s": r["duration"],
            })
        return routes

    except Exception as e:
        logger.error(f"OSRM request failed: {e}")
        return []


def rank_routes_by_safety(routes: List[Dict], engine) -> List[Dict]:
    """
    Check each OSRM route for hazards and rank by safety.

    Returns routes sorted by risk score (lowest first) with hazard info attached.
    """
    for route_data in routes:
        line = LineString(route_data["coordinates"])
        hazards = check_hazards_along_route(engine, line)
        score, level = calculate_risk_score(hazards)
        route_data["hazards"] = hazards
        route_data["risk_score"] = score
        route_data["risk_level"] = level
        route_data["hazard_count"] = len(hazards)

    routes.sort(key=lambda r: r["risk_score"])
    return routes


# ------------------------------------------------------------------
# Main API
# ------------------------------------------------------------------

def check_route(
    coordinates: Optional[List[List[float]]] = None,
    wkt_string: Optional[str] = None,
    buffer_meters: float = ROUTE_BUFFER_METERS,
    min_severity: int = 1,
) -> Dict:
    if coordinates:
        route = parse_route_coordinates(coordinates)
    elif wkt_string:
        route = parse_route_wkt(wkt_string)
    else:
        raise ValueError("Must provide either coordinates or wkt_string")

    engine = get_engine()
    hazards = check_hazards_along_route(engine, route, buffer_meters, min_severity)
    score, level = calculate_risk_score(hazards)

    return {
        "checked_at": datetime.now(CHICAGO_TZ).isoformat(),
        "buffer_meters": buffer_meters,
        "route_length_km": calculate_route_length_km(route),
        "risk_assessment": {
            "score": score,
            "level": level,
            "message": risk_message(level, len(hazards)),
            "hazard_count": len(hazards),
            "highest_severity": max((h["severity"] for h in hazards), default=0),
        },
        "hazards": hazards,
    }


def find_safe_route(start: List[float], end: List[float]) -> Dict:
    """
    High-level API: get OSRM routes, rank by safety, return best.

    Args:
        start: [longitude, latitude]
        end: [longitude, latitude]
    """
    engine = get_engine()
    routes = get_osrm_route(start, end, alternatives=routing_config.max_alternatives)

    if not routes:
        return {"error": "No routes found", "routes": []}

    ranked = rank_routes_by_safety(routes, engine)
    return {
        "checked_at": datetime.now(CHICAGO_TZ).isoformat(),
        "origin": start,
        "destination": end,
        "routes": ranked,
        "recommended": ranked[0] if ranked else None,
    }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="AirScout Route Hazard Checker")
    parser.add_argument("--coords", type=str, help="Route coordinates as JSON")
    parser.add_argument("--wkt", type=str, help="Route as WKT LINESTRING")
    parser.add_argument("--start", type=str, help="Start [lon,lat] for safe-route")
    parser.add_argument("--end", type=str, help="End [lon,lat] for safe-route")
    parser.add_argument("--buffer", type=float, default=ROUTE_BUFFER_METERS)
    parser.add_argument("--min-severity", type=int, default=1)
    args = parser.parse_args()

    if args.start and args.end:
        start = json.loads(args.start)
        end = json.loads(args.end)
        result = find_safe_route(start, end)
        print(json.dumps(result, indent=2))
        return

    coordinates = json.loads(args.coords) if args.coords else None

    if not coordinates and not args.wkt:
        logger.info("No route provided \u2014 using demo route")
        coordinates = [[-87.6298, 41.8781], [-87.6350, 41.8850], [-87.6400, 41.9000], [-87.6450, 41.9150]]

    result = check_route(coordinates=coordinates, wkt_string=args.wkt, buffer_meters=args.buffer, min_severity=args.min_severity)

    risk = result["risk_assessment"]
    print(f"\nRisk Level: {risk['level']} (Score: {risk['score']}/100)")
    print(f"  {risk['message']}")
    print(f"  Route length: {result['route_length_km']} km")
    print(f"  Hazards found: {risk['hazard_count']}")

    for h in result["hazards"][:10]:
        icon = {"PERMIT": "P", "TRAFFIC": "T", "SCHOOL": "S", "AQI": "A"}.get(h["type"], "?")
        print(f"\n  [{icon}] {h['type']} (Severity {h['severity']}/5)")
        print(f"      {h['description'][:60]}")
        print(f"      Distance: {h['distance_meters']}m from route")


if __name__ == "__main__":
    main()
