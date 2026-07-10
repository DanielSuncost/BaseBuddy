"""Tests for the storage estimation planner."""
from basebuddy.core.storage_estimate import estimate_storage


class TestEstimateStorage:
    def test_detections_mode(self):
        result = estimate_storage({
            "cameras": 4,
            "retention_days": 30,
            "mode": "detections",
            "detections_per_cam_day": 100,
        })
        assert result["total_gb"] > 0
        assert any("Detection" in line["label"] for line in result["breakdown"])

    def test_mixed_mode_has_both_lines(self):
        result = estimate_storage({
            "cameras": 2,
            "retention_days": 7,
            "mode": "mixed",
            "detections_per_cam_day": 50,
            "still_interval_sec": 60,
        })
        labels = [line["label"] for line in result["breakdown"]]
        assert len(labels) == 2

    def test_more_days_means_more_storage(self):
        small = estimate_storage({"cameras": 1, "retention_days": 7, "mode": "detections"})
        large = estimate_storage({"cameras": 1, "retention_days": 70, "mode": "detections"})
        assert large["total_gb"] > small["total_gb"]

    def test_defaults_are_safe(self):
        # Empty payload should not raise and should clamp to at least 1 camera / 1 day.
        result = estimate_storage({})
        assert result["total_gb"] >= 0
