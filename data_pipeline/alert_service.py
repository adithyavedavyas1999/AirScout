"""
AirScout Alert Service
======================

Core service for checking user routes against active hazards and
triggering push notifications when hazards are detected.

Flow:
1. Fetch all active user subscriptions
2. For each subscription, check route against hazards (25m buffer)
3. If new hazards found, send push notification
4. Track sent alerts to avoid duplicates
"""

import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Tuple
from zoneinfo import ZoneInfo
from dataclasses import dataclass

import pandas as pd
import geopandas as gpd
from shapely import wkt
from sqlalchemy import text

from data_pipeline.db import get_engine
from data_pipeline.scoring import calculate_risk_score, ROUTE_BUFFER_METERS

CHICAGO_TZ = ZoneInfo("America/Chicago")

ALERT_COOLDOWN_HOURS = 4
MIN_SEVERITY_FOR_ALERT = 3
PUSH_BATCH_SIZE = 100

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class RouteAlert:
    subscription_id: str
    user_id: str
    route_name: str
    push_token: str
    hazards: List[Dict]
    risk_score: int
    risk_level: str


@dataclass
class AlertResult:
    subscriptions_checked: int
    alerts_generated: int
    notifications_sent: int
    errors: List[str]


# ------------------------------------------------------------------
# Subscription Management
# ------------------------------------------------------------------

def fetch_active_subscriptions(engine) -> pd.DataFrame:
    query = """
        SELECT
            id, user_id, route_name,
            ST_AsText(route_geometry) as route_wkt,
            push_token, severity_threshold, created_at
        FROM user_subscriptions
        WHERE alert_enabled = TRUE
          AND push_token IS NOT NULL
          AND push_token != ''
    """
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    logger.info(f"Fetched {len(df)} active subscriptions")
    return df


def get_recent_alerts(engine, user_id: str, hours: int = ALERT_COOLDOWN_HOURS) -> List[str]:
    """Get hazard IDs recently alerted to this user (parameterized interval)."""
    query = text("""
        SELECT DISTINCT hazard_source_id
        FROM alert_history
        WHERE user_id = :user_id
          AND sent_at > NOW() - make_interval(hours => :hours)
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"user_id": user_id, "hours": hours})
        return [row[0] for row in result]


def record_alert(engine, user_id: str, subscription_id: str, hazard_source_ids: List[str]) -> None:
    query = text("""
        INSERT INTO alert_history (user_id, subscription_id, hazard_source_id, sent_at)
        VALUES (:user_id, :subscription_id, :hazard_source_id, NOW())
    """)
    with engine.connect() as conn:
        for hazard_id in hazard_source_ids:
            conn.execute(query, {
                "user_id": user_id,
                "subscription_id": subscription_id,
                "hazard_source_id": hazard_id,
            })
        conn.commit()


# ------------------------------------------------------------------
# Hazard Detection
# ------------------------------------------------------------------

def check_route_for_hazards(
    engine,
    route_wkt: str,
    severity_threshold: int = MIN_SEVERITY_FOR_ALERT,
    exclude_hazard_ids: List[str] | None = None,
) -> Tuple[List[Dict], int, str]:
    if exclude_hazard_ids is None:
        exclude_hazard_ids = []

    route = wkt.loads(route_wkt)
    route_gdf = gpd.GeoDataFrame({"geometry": [route]}, crs="EPSG:4326")
    route_projected = route_gdf.to_crs("EPSG:26971")
    route_projected["geometry"] = route_projected.geometry.buffer(ROUTE_BUFFER_METERS)
    buffered = route_projected.to_crs("EPSG:4326")
    buffer_wkt = buffered.geometry.iloc[0].wkt

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
          AND severity >= :severity_threshold
          AND ST_Intersects(location, ST_GeomFromText(:buffer_wkt, 4326))
          AND (:no_exclusions OR source_id != ALL(:exclude_ids))
        ORDER BY severity DESC, distance_meters ASC
    """)

    hazards = []
    with engine.connect() as conn:
        result = conn.execute(query, {
            "route_wkt": route_wkt,
            "buffer_wkt": buffer_wkt,
            "severity_threshold": severity_threshold,
            "no_exclusions": len(exclude_hazard_ids) == 0,
            "exclude_ids": exclude_hazard_ids if exclude_hazard_ids else ["__none__"],
        })

        for row in result:
            hazards.append({
                "id": str(row.id),
                "type": row.type,
                "severity": row.severity,
                "description": row.description,
                "source_id": row.source_id,
                "longitude": float(row.longitude),
                "latitude": float(row.latitude),
                "distance_meters": round(row.distance_meters, 1),
            })

    risk_score, risk_level = calculate_risk_score(hazards)
    return hazards, risk_score, risk_level


# ------------------------------------------------------------------
# Push Notifications
# ------------------------------------------------------------------

def build_notification_payload(alert: RouteAlert) -> Dict:
    hazard_count = len(alert.hazards)
    highest_severity = max(h["severity"] for h in alert.hazards)

    if alert.risk_level == "HIGH":
        title = f"\u26a0\ufe0f High Risk Alert: {alert.route_name}"
    elif alert.risk_level == "MODERATE":
        title = f"\u26a1 Hazard Alert: {alert.route_name}"
    else:
        title = f"\u2139\ufe0f Route Update: {alert.route_name}"

    hazard_types = set(h["type"] for h in alert.hazards)
    type_icons = {
        "PERMIT": "\U0001f3d7\ufe0f Demolition",
        "TRAFFIC": "\U0001f697 Traffic",
        "SCHOOL": "\U0001f3eb School Zone",
        "AQI": "\U0001f32b\ufe0f Air Quality",
    }
    type_summary = ", ".join([type_icons.get(t, t) for t in hazard_types])
    body = f"{hazard_count} hazard{'s' if hazard_count > 1 else ''} detected: {type_summary}"

    return {
        "title": title,
        "body": body,
        "data": {
            "type": "hazard_alert",
            "route_name": alert.route_name,
            "risk_score": alert.risk_score,
            "risk_level": alert.risk_level,
            "hazard_count": hazard_count,
            "highest_severity": highest_severity,
            "hazards": json.dumps(alert.hazards[:5]),
        },
        "icon": "/icons/icon-192.png",
        "badge": "/icons/icon-72.png",
        "tag": f"route-{alert.subscription_id}",
        "renotify": True,
    }


def send_web_push_notification(push_token: str, payload: Dict, vapid_private_key: str | None = None) -> bool:
    try:
        from pywebpush import webpush

        subscription_info = json.loads(push_token)
        vapid_private_key = vapid_private_key or os.environ.get("VAPID_PRIVATE_KEY")
        vapid_email = os.environ.get("VAPID_EMAIL", "mailto:admin@airscout.app")

        if not vapid_private_key:
            logger.warning("VAPID_PRIVATE_KEY not set")
            return False

        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_email},
        )
        return True

    except ImportError:
        logger.warning("pywebpush not installed")
        return False
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")
        return False


def send_notifications_batch(alerts: List[RouteAlert]) -> Tuple[int, List[str]]:
    sent = 0
    errors = []
    for alert in alerts:
        payload = build_notification_payload(alert)
        if send_web_push_notification(alert.push_token, payload):
            sent += 1
            logger.info(f"Sent notification to {alert.user_id} for route {alert.route_name}")
        else:
            errors.append(f"Failed to send to user {alert.user_id}")
    return sent, errors


# ------------------------------------------------------------------
# Main Processing
# ------------------------------------------------------------------

def process_alerts(dry_run: bool = False) -> AlertResult:
    logger.info("=" * 60)
    logger.info("AirScout Alert Service")
    logger.info(f"Time: {datetime.now(CHICAGO_TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if dry_run:
        logger.info("*** DRY RUN MODE ***")
    logger.info("=" * 60)

    engine = get_engine()
    subscriptions_df = fetch_active_subscriptions(engine)

    if subscriptions_df.empty:
        logger.info("No active subscriptions found")
        return AlertResult(0, 0, 0, [])

    alerts_to_send: List[RouteAlert] = []
    all_errors: List[str] = []

    for _, sub in subscriptions_df.iterrows():
        try:
            recent_alerts = [] if dry_run else get_recent_alerts(engine, sub["user_id"])

            hazards, risk_score, risk_level = check_route_for_hazards(
                engine,
                sub["route_wkt"],
                severity_threshold=sub.get("severity_threshold", MIN_SEVERITY_FOR_ALERT),
                exclude_hazard_ids=recent_alerts,
            )

            if hazards:
                alert = RouteAlert(
                    subscription_id=str(sub["id"]),
                    user_id=sub["user_id"],
                    route_name=sub["route_name"] or "My Route",
                    push_token=sub["push_token"],
                    hazards=hazards,
                    risk_score=risk_score,
                    risk_level=risk_level,
                )
                alerts_to_send.append(alert)
                logger.info(
                    f"Alert for {sub['user_id']}: {len(hazards)} hazards "
                    f"on route '{sub['route_name']}' (risk={risk_level})"
                )

        except Exception as e:
            error_msg = f"Error processing subscription {sub['id']}: {e}"
            logger.error(error_msg)
            all_errors.append(error_msg)

    sent_count = 0
    if alerts_to_send and not dry_run:
        sent_count, send_errors = send_notifications_batch(alerts_to_send)
        all_errors.extend(send_errors)

        for alert in alerts_to_send:
            hazard_ids = [h["source_id"] for h in alert.hazards]
            record_alert(engine, alert.user_id, alert.subscription_id, hazard_ids)

    elif alerts_to_send and dry_run:
        logger.info(f"\nWould send {len(alerts_to_send)} notifications:")
        for alert in alerts_to_send:
            payload = build_notification_payload(alert)
            logger.info(f"  To: {alert.user_id}")
            logger.info(f"  Title: {payload['title']}")
            logger.info(f"  Body: {payload['body']}")
        sent_count = len(alerts_to_send)

    result = AlertResult(
        subscriptions_checked=len(subscriptions_df),
        alerts_generated=len(alerts_to_send),
        notifications_sent=sent_count,
        errors=all_errors,
    )

    logger.info("=" * 60)
    logger.info(f"Subscriptions checked: {result.subscriptions_checked}")
    logger.info(f"Alerts generated: {result.alerts_generated}")
    logger.info(f"Notifications sent: {result.notifications_sent}")
    if result.errors:
        logger.warning(f"Errors: {len(result.errors)}")
    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AirScout Alert Service")
    parser.add_argument("--dry-run", action="store_true", help="Run without sending notifications")
    args = parser.parse_args()
    process_alerts(dry_run=args.dry_run)
