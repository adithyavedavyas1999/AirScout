"""Tests for the configuration module."""

from data_pipeline.config import (
    chicago_data,
    zombie_permit,
    school_zone,
    geospatial,
    aqi,
    weather,
    routing,
    multi_city,
)


class TestChicagoDataConfig:
    def test_datasets_are_strings(self):
        assert isinstance(chicago_data.permits_dataset, str)
        assert isinstance(chicago_data.traffic_dataset, str)
        assert isinstance(chicago_data.schools_dataset, str)

    def test_traffic_dataset_is_sxs8(self):
        assert chicago_data.traffic_dataset == "sxs8-h27x"


class TestZombiePermitConfig:
    def test_defaults(self):
        assert zombie_permit.complaint_radius_meters == 200.0
        assert zombie_permit.complaint_lookback_hours == 48
        assert zombie_permit.hazard_expiration_hours == 168

    def test_permit_types(self):
        assert len(zombie_permit.permit_types) >= 1
        assert any("DEMOLITION" in t for t in zombie_permit.permit_types)


class TestSchoolZoneConfig:
    def test_peak_hours(self):
        assert school_zone.morning_start == "07:00"
        assert school_zone.afternoon_end == "16:00"

    def test_active_days_weekdays_only(self):
        assert school_zone.active_days == [0, 1, 2, 3, 4]


class TestGeospatialConfig:
    def test_buffer_defaults(self):
        assert geospatial.route_buffer_meters == 25.0
        assert geospatial.wgs84_srid == 4326
        assert geospatial.illinois_srid == 26971


class TestNewFeatureConfigs:
    def test_aqi_config(self):
        assert aqi.min_aqi_for_hazard == 101
        assert "airnow" in aqi.base_url.lower()

    def test_weather_config(self):
        assert weather.chicago_lat > 41
        assert weather.wind_speed_amplifier_threshold_mph > 0

    def test_routing_config(self):
        assert routing.profile == "foot"
        assert routing.max_alternatives >= 2

    def test_multi_city_chicago(self):
        assert "chicago" in multi_city.cities
        assert multi_city.cities["chicago"]["srid_local"] == 26971
