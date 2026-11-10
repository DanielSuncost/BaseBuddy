"""Tests for plant detail plot aggregations (daily deltas)."""
from __future__ import annotations

from basebuddy.plugins.plant_health.plot_stats import daily_metric_deltas


def test_daily_metric_deltas_empty():
    assert daily_metric_deltas([]) == []
    assert daily_metric_deltas(None) == []


def test_daily_metric_deltas_one_day_no_delta():
    t0 = 1_700_000_000.0  # fixed epoch
    out = daily_metric_deltas([
        {"sampled_at": t0, "G_norm": 1.0, "sample_ok": True},
        {"sampled_at": t0 + 3600, "G_norm": 1.1, "sample_ok": True},
    ])
    assert len(out) == 1
    assert out[0]["value"] == 1.1  # last sample of the day
    assert out[0]["delta"] is None


def test_daily_metric_deltas_day_over_day():
    # Two UTC midnights apart
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    day2 = day0 + 2 * 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.00, "sample_ok": True},
        {"sampled_at": day1, "G_norm": 0.95, "sample_ok": True},
        {"sampled_at": day2, "G_norm": 1.05, "sample_ok": True},
    ])
    assert len(out) == 3
    assert out[0]["delta"] is None
    assert out[1]["delta"] == -0.05
    assert out[2]["delta"] == 0.1


def test_daily_metric_deltas_skips_poor_quality():
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.0, "sample_ok": True},
        {"sampled_at": day1, "G_norm": 0.2, "sample_ok": False},
        {"sampled_at": day1 + 100, "G_norm": 0.9, "sample_ok": True},
    ])
    assert len(out) == 2
    assert out[1]["value"] == 0.9
    assert out[1]["delta"] == -0.1


def test_daily_metric_deltas_can_include_poor():
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.0, "sample_ok": False},
        {"sampled_at": day1, "G_norm": 0.8, "sample_ok": False},
    ], good_only=False)
    assert len(out) == 2
    assert out[1]["delta"] == -0.2


def test_daily_metric_deltas_skips_low_light_even_if_sample_ok_null():
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.0, "sample_ok": True, "L_bg": 0.5},
        {"sampled_at": day1, "G_norm": 8.0, "sample_ok": None, "L_bg": 0.02},
        {"sampled_at": day1 + 100, "G_norm": 0.95, "sample_ok": True, "L_bg": 0.45},
    ])
    assert len(out) == 2
    assert out[1]["value"] == 0.95
    assert out[1]["delta"] == -0.05


def test_daily_metric_deltas_skips_dim_light():
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.0, "sample_ok": True, "L_bg": 0.5, "L_frame": 0.5},
        {"sampled_at": day1, "G_norm": 0.87, "sample_ok": True, "L_bg": 0.38, "L_frame": 0.39},
    ])
    assert len(out) == 1
    assert out[0]["value"] == 1.0


def test_daily_metric_deltas_skips_spike_without_lighting():
    day0 = 1_700_000_000.0
    day1 = day0 + 86400
    out = daily_metric_deltas([
        {"sampled_at": day0, "G_norm": 1.0, "sample_ok": True},
        {"sampled_at": day1, "G_norm": 9.5, "sample_ok": None},  # spike, no L_*
    ])
    assert len(out) == 1
    assert out[0]["value"] == 1.0
