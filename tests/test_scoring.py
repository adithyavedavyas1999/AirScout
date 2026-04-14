"""Tests for the centralized risk scoring module."""

from data_pipeline.scoring import calculate_risk_score, risk_message


class TestCalculateRiskScore:
    def test_empty_hazards_returns_zero(self):
        score, level = calculate_risk_score([])
        assert score == 0
        assert level == "LOW"

    def test_single_close_severe_hazard(self):
        hazards = [{"distance_meters": 0, "severity": 5}]
        score, level = calculate_risk_score(hazards)
        assert score == 25
        assert level == "LOW"

    def test_multiple_close_hazards_high_risk(self):
        hazards = [
            {"distance_meters": 0, "severity": 5},
            {"distance_meters": 5, "severity": 5},
            {"distance_meters": 10, "severity": 4},
        ]
        score, level = calculate_risk_score(hazards)
        assert score >= 40
        assert level in ("MODERATE", "HIGH")

    def test_far_hazard_contributes_nothing(self):
        hazards = [{"distance_meters": 25, "severity": 5}]
        score, level = calculate_risk_score(hazards)
        assert score == 0
        assert level == "LOW"

    def test_beyond_buffer_hazard_ignored(self):
        hazards = [{"distance_meters": 30, "severity": 5}]
        score, level = calculate_risk_score(hazards)
        assert score == 0

    def test_score_capped_at_100(self):
        hazards = [{"distance_meters": 0, "severity": 5} for _ in range(50)]
        score, _ = calculate_risk_score(hazards)
        assert score == 100

    def test_high_threshold(self):
        hazards = [{"distance_meters": 0, "severity": 5} for _ in range(10)]
        score, level = calculate_risk_score(hazards)
        assert level == "HIGH"
        assert score >= 70

    def test_moderate_threshold(self):
        hazards = [
            {"distance_meters": 0, "severity": 5},
            {"distance_meters": 5, "severity": 4},
            {"distance_meters": 10, "severity": 3},
        ]
        score, level = calculate_risk_score(hazards)
        assert score >= 40
        assert level == "MODERATE"

    def test_custom_buffer(self):
        hazards = [{"distance_meters": 40, "severity": 5}]
        score, _ = calculate_risk_score(hazards, buffer_meters=50)
        assert score > 0

    def test_severity_1_low_contribution(self):
        hazards = [{"distance_meters": 0, "severity": 1}]
        score, level = calculate_risk_score(hazards)
        assert score == 5
        assert level == "LOW"


class TestRiskMessage:
    def test_no_hazards(self):
        msg = risk_message("LOW", 0)
        assert "No hazards" in msg

    def test_high_risk(self):
        msg = risk_message("HIGH", 5)
        assert "alternate" in msg.lower()

    def test_moderate_risk(self):
        msg = risk_message("MODERATE", 3)
        assert "aware" in msg.lower()

    def test_low_risk(self):
        msg = risk_message("LOW", 1)
        assert "clear" in msg.lower()
