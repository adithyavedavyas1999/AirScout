"""
AirScout Configuration Module
============================

Central configuration for all data pipeline settings.
Loads from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass
from typing import List


@dataclass
class ChicagoDataPortalConfig:
    """Chicago Data Portal API configuration."""
    base_url: str = "data.cityofchicago.org"
    
    # Dataset IDs (Socrata)
    permits_dataset: str = "ydr8-5enu"  # Building Permits
    complaints_311_dataset: str = "v6vf-nfxy"  # 311 Service Requests
    schools_dataset: str = "9xs2-f89t"  # Chicago Public Schools
    traffic_dataset: str = "85ca-t3if"  # Traffic Tracker (Congestion)
    
    # App token (optional, increases rate limit)
    app_token: str = os.environ.get("CHICAGO_DATA_APP_TOKEN", "")
    
    # Rate limiting
    max_requests_per_hour: int = 1000  # Unauthenticated limit


@dataclass
class ZombiePermitConfig:
    """Configuration for the Zombie Permit validation logic."""
    # Spatial parameters
    complaint_radius_meters: float = 200.0  # Complaints must be within 200m
    
    # Temporal parameters
    complaint_lookback_hours: int = 48  # Only recent complaints
    hazard_expiration_hours: int = 168  # 7 days
    
    # Permit types to track
    permit_types: List[str] = None
    
    # 311 complaint types that validate permits
    validating_complaint_types: List[str] = None
    
    def __post_init__(self):
        if self.permit_types is None:
            self.permit_types = [
                "PERMIT - WRECKING/DEMOLITION",
                "WRECKING/DEMOLITION",
            ]
        if self.validating_complaint_types is None:
            self.validating_complaint_types = [
                "SVR",  # Severe Weather/Road condition
                "NOI",  # Noise complaint
            ]


@dataclass
class SchoolZoneConfig:
    """Configuration for the School Zone hard-coded logic."""
    # Time windows when school zones are HIGH RISK
    morning_start: str = "07:00"
    morning_end: str = "09:00"
    afternoon_start: str = "14:00"
    afternoon_end: str = "16:00"
    
    # School zone radius
    zone_radius_meters: float = 150.0
    
    # Hard-coded severity during peak hours
    peak_severity: int = 5
    
    # Days when school zones apply (0=Monday, 6=Sunday)
    active_days: List[int] = None
    
    def __post_init__(self):
        if self.active_days is None:
            self.active_days = [0, 1, 2, 3, 4]  # Monday-Friday


@dataclass
class GeospatialBufferConfig:
    """Configuration for the Geospatial Buffer logic."""
    # Buffer around user routes to catch adjacent hazards
    route_buffer_meters: float = 25.0
    
    # SRID for projections
    wgs84_srid: int = 4326  # Standard GPS coordinates
    illinois_srid: int = 26971  # Illinois State Plane East (meters)


@dataclass
class SupabaseConfig:
    """Supabase database configuration."""
    host: str = os.environ.get("SUPABASE_DB_HOST", "")
    port: str = os.environ.get("SUPABASE_DB_PORT", "5432")
    database: str = os.environ.get("SUPABASE_DB_NAME", "postgres")
    user: str = os.environ.get("SUPABASE_DB_USER", "postgres")
    password: str = os.environ.get("SUPABASE_DB_PASSWORD", "")
    
    @property
    def connection_url(self) -> str:
        """Build PostgreSQL connection URL."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
    
    def validate(self) -> bool:
        """Check if required config is present."""
        return bool(self.host and self.password)


# Global configuration instances
chicago_data = ChicagoDataPortalConfig()
zombie_permit = ZombiePermitConfig()
school_zone = SchoolZoneConfig()
geospatial = GeospatialBufferConfig()
supabase = SupabaseConfig()


