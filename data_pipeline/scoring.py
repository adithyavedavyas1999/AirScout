"""
AirScout Centralized Risk Scoring
===================================

Single source of truth for risk score calculation used by:
- check_route.py (Python CLI)
- alert_service.py (Python alert pipeline)
- Edge function (references these same constants)
- PWA (references these same thresholds)

All risk scoring constants and logic live here to prevent drift.
"""

from typing import List, Dict, Tuple

ROUTE_BUFFER_METERS = 25

RISK_WEIGHT_MULTIPLIER = 25

RISK_THRESHOLD_HIGH = 70
RISK_THRESHOLD_MODERATE = 40

SEVERITY_MAX = 5


def calculate_risk_score(hazards: List[Dict], buffer_meters: float = ROUTE_BUFFER_METERS) -> Tuple[int, str]:
    """
    Calculate a 0-100 risk score and level from hazards.

    Each hazard contributes a weighted score based on:
      - Proximity (closer = more impact)
      - Severity (higher = more impact)

    Returns:
        (score, level) where level is HIGH / MODERATE / LOW
    """
    if not hazards:
        return 0, "LOW"

    total = 0.0
    for h in hazards:
        distance = h.get("distance_meters", 0)
        severity = h.get("severity", 1)

        distance_weight = max(0.0, 1 - (distance / buffer_meters))
        severity_weight = severity / SEVERITY_MAX
        total += distance_weight * severity_weight * RISK_WEIGHT_MULTIPLIER

    score = min(100, int(total))

    if score >= RISK_THRESHOLD_HIGH:
        level = "HIGH"
    elif score >= RISK_THRESHOLD_MODERATE:
        level = "MODERATE"
    else:
        level = "LOW"

    return score, level


def risk_message(level: str, hazard_count: int) -> str:
    """Human-readable risk message."""
    messages = {
        "HIGH": "High pollution risk \u2014 consider an alternate route",
        "MODERATE": "Moderate pollution risk \u2014 be aware of hazards",
        "LOW": "Low pollution risk \u2014 route is relatively clear",
    }
    if hazard_count == 0:
        return "No hazards detected along this route"
    return messages.get(level, messages["LOW"])
