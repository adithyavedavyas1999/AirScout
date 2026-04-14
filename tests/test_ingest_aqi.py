"""Tests for AQI ingestion logic."""

from data_pipeline.ingest_aqi import aqi_to_severity


class TestAqiToSeverity:
    def test_good_aqi(self):
        severity, label = aqi_to_severity(25)
        assert severity == 1
        assert label == "Good"

    def test_moderate_aqi(self):
        severity, label = aqi_to_severity(75)
        assert severity == 2
        assert label == "Moderate"

    def test_unhealthy_sensitive(self):
        severity, label = aqi_to_severity(120)
        assert severity == 3
        assert "Sensitive" in label

    def test_unhealthy(self):
        severity, label = aqi_to_severity(175)
        assert severity == 4

    def test_very_unhealthy(self):
        severity, label = aqi_to_severity(250)
        assert severity == 5

    def test_hazardous(self):
        severity, label = aqi_to_severity(350)
        assert severity == 5
        assert label == "Hazardous"

    def test_zero_aqi(self):
        severity, label = aqi_to_severity(0)
        assert severity == 1
