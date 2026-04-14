"""Tests for weather wind amplifier logic."""

from data_pipeline.ingest_weather import get_wind_amplifier


class TestWindAmplifier:
    def test_no_weather_data(self):
        assert get_wind_amplifier(None) == 1.0

    def test_calm_wind(self):
        assert get_wind_amplifier({"wind_speed_mph": 5}) == 1.0

    def test_at_threshold(self):
        assert get_wind_amplifier({"wind_speed_mph": 15}) == 1.0

    def test_above_threshold(self):
        amp = get_wind_amplifier({"wind_speed_mph": 22.5})
        assert amp == 1.5

    def test_very_high_wind_capped(self):
        amp = get_wind_amplifier({"wind_speed_mph": 100})
        assert amp == 2.0

    def test_double_threshold(self):
        amp = get_wind_amplifier({"wind_speed_mph": 30})
        assert amp == 2.0
