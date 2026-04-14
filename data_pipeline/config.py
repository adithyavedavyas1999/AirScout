"""
AirScout Configuration Module
============================

Central configuration for all data pipeline settings.
Loads from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class ChicagoDataPortalConfig:
    """Chicago Data Portal API configuration."""
    base_url: str = "data.cityofchicago.org"

    permits_dataset: str = "ydr8-5enu"
    complaints_311_dataset: str = "v6vf-nfxy"
    schools_dataset: str = "9xs2-f89t"
    traffic_dataset: str = "sxs8-h27x"

    app_token: str = ""

    max_requests_per_hour: int = 1000

    def __post_init__(self):
        self.app_token = os.environ.get("CHICAGO_DATA_APP_TOKEN", "")


@dataclass
class ZombiePermitConfig:
    """Configuration for the Zombie Permit validation logic."""
    complaint_radius_meters: float = 200.0
    complaint_lookback_hours: int = 48
    hazard_expiration_hours: int = 168

    permit_types: List[str] = field(default_factory=lambda: [
        "PERMIT - WRECKING/DEMOLITION",
        "WRECKING/DEMOLITION",
    ])

    validating_complaint_types: List[str] = field(default_factory=lambda: [
        "SVR",
        "NOI",
    ])


@dataclass
class SchoolZoneConfig:
    """Configuration for the School Zone hard-coded logic."""
    morning_start: str = "07:00"
    morning_end: str = "09:00"
    afternoon_start: str = "14:00"
    afternoon_end: str = "16:00"

    zone_radius_meters: float = 150.0
    peak_severity: int = 5

    active_days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])


@dataclass
class GeospatialBufferConfig:
    """Configuration for the Geospatial Buffer logic."""
    route_buffer_meters: float = 25.0
    wgs84_srid: int = 4326
    illinois_srid: int = 26971


@dataclass
class AQIConfig:
    """EPA AirNow AQI integration settings."""
    api_key: str = ""
    base_url: str = "https://www.airnowapi.org/aq"
    bbox_chicago: str = "-88.0,41.6,-87.4,42.2"
    update_interval_minutes: int = 30
    hazard_expiration_minutes: int = 60
    min_aqi_for_hazard: int = 101

    def __post_init__(self):
        self.api_key = os.environ.get("AIRNOW_API_KEY", "")


@dataclass
class WeatherConfig:
    """OpenWeatherMap integration for wind-adjusted scoring."""
    api_key: str = ""
    base_url: str = "https://api.openweathermap.org/data/2.5"
    chicago_lat: float = 41.8781
    chicago_lon: float = -87.6298
    wind_speed_amplifier_threshold_mph: float = 15.0

    def __post_init__(self):
        self.api_key = os.environ.get("OPENWEATHER_API_KEY", "")


@dataclass
class RoutingConfig:
    """OSRM routing engine configuration."""
    osrm_base_url: str = "https://router.project-osrm.org"
    profile: str = "foot"
    max_alternatives: int = 3
    hazard_penalty_weight: float = 2.0


@dataclass
class MultiCityConfig:
    """Abstraction layer for multi-city support."""
    current_city: str = "chicago"
    cities: dict = field(default_factory=lambda: {
        "chicago": {
            "name": "Chicago",
            "center": [41.8781, -87.6298],
            "srid_local": 26971,
            "timezone": "America/Chicago",
            "data_portal": "data.cityofchicago.org",
        }
    })


chicago_data = ChicagoDataPortalConfig()
zombie_permit = ZombiePermitConfig()
school_zone = SchoolZoneConfig()
geospatial = GeospatialBufferConfig()
aqi = AQIConfig()
weather = WeatherConfig()
routing = RoutingConfig()
multi_city = MultiCityConfig()
