"""Tests for plant color timeline ordering and trend helpers."""
from __future__ import annotations

from basebuddy.plugins.plant_health.db import compute_color_trends


def test_compute_color_trends_empty():
    t = compute_color_trends([])
    assert t["sample_count"] == 0
    assert t["declining_greenness"] is False
    assert t["greenness_rate_per_day"] is None


def test_compute_color_trends_declining():
    # ~7 days of declining greenness (legacy rows without G_norm)
    t0 = 1_700_000_000.0
    samples = [
        {"sampled_at": t0, "greenness": 0.50, "coverage": 0.12, "plant_pixels": 10000},
        {"sampled_at": t0 + 3 * 86400, "greenness": 0.46, "coverage": 0.11, "plant_pixels": 9500},
        {"sampled_at": t0 + 7 * 86400, "greenness": 0.42, "coverage": 0.10, "plant_pixels": 9000},
    ]
    t = compute_color_trends(samples)
    assert t["sample_count"] == 3
    assert t["declining_greenness"] is True
    assert t["greenness_rate_per_day"] is not None
    assert t["greenness_rate_per_day"] < 0
    assert t["leaf_area_proxy"] == 9000
    assert t["window_hours"] == 7 * 24
    assert t["trend_metric"] == "greenness"


def test_compute_color_trends_uses_g_norm_when_available():
    t0 = 1_700_000_000.0
    samples = [
        {"sampled_at": t0, "greenness": 0.50, "G_rel": 0.50, "G_norm": 1.00,
         "coverage": 0.12, "plant_pixels": 10000, "sample_ok": True},
        {"sampled_at": t0 + 3 * 86400, "greenness": 0.49, "G_rel": 0.49, "G_norm": 0.90,
         "coverage": 0.12, "plant_pixels": 10000, "sample_ok": True},
        {"sampled_at": t0 + 7 * 86400, "greenness": 0.48, "G_rel": 0.48, "G_norm": 0.80,
         "coverage": 0.12, "plant_pixels": 10000, "sample_ok": True},
    ]
    t = compute_color_trends(samples)
    assert t["trend_metric"] == "G_norm"
    assert t["declining_greenness"] is True
    assert t["G_norm_latest"] == 0.80


def test_compute_color_trends_skips_poor_quality_for_decline():
    t0 = 1_700_000_000.0
    # Values drop hard but all marked poor quality → no decline alert
    samples = [
        {"sampled_at": t0, "greenness": 0.50, "G_rel": 0.50, "G_norm": 1.0,
         "coverage": 0.1, "sample_ok": False},
        {"sampled_at": t0 + 3 * 86400, "greenness": 0.40, "G_rel": 0.40, "G_norm": 0.7,
         "coverage": 0.1, "sample_ok": False},
        {"sampled_at": t0 + 7 * 86400, "greenness": 0.30, "G_rel": 0.30, "G_norm": 0.4,
         "coverage": 0.1, "sample_ok": False},
    ]
    t = compute_color_trends(samples)
    assert t["good_sample_count"] == 0
    assert t["declining_greenness"] is False


def test_compute_color_trends_growing_coverage():
    t0 = 1_700_000_000.0
    samples = [
        {"sampled_at": t0, "greenness": 0.45, "coverage": 0.08, "plant_pixels": 8000},
        {"sampled_at": t0 + 10 * 86400, "greenness": 0.46, "coverage": 0.15, "plant_pixels": 15000},
    ]
    t = compute_color_trends(samples)
    assert t["growing_coverage"] is True
    assert t["declining_greenness"] is False
    assert t["coverage_rate_per_day"] > 0


def test_list_color_timeline_newest_first_then_asc(monkeypatch):
    """Ensure LIMIT applies to newest rows, returned chronologically."""
    import basebuddy.plugins.plant_health.db as dbmod

    class FakeCur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class FakeConn:
        def __init__(self, rows):
            self._rows = rows
            self.last_sql = None
            self.last_params = None

        def execute(self, sql, params=None):
            self.last_sql = sql
            self.last_params = params
            return FakeCur(self._rows)

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # Pretend DB returns newest-first (as our query requests), including new cols
    # sampled_at, coverage, h,s,v, r,g,b, greenness, dom, image, pixels,
    # G_rel, L_bg, G_norm, yellow_fraction, sample_ok, sample_quality,
    # L_frame, L_plant, quality_flags
    rows_desc = [
        (300.0, 0.2, 40, 50, 60, 10, 80, 20, 0.55, "[]", None, 2000,
         0.55, 0.5, 1.1, 0.02, 1, "ok", 0.52, 0.48, '[]'),
        (200.0, 0.15, 40, 50, 60, 10, 70, 20, 0.50, "[]", None, 1500,
         0.50, 0.5, 1.0, 0.03, 1, "ok", 0.50, 0.46, '[]'),
        (100.0, 0.1, 40, 50, 60, 10, 60, 20, 0.45, "[]", None, 1000,
         0.45, 0.5, 0.9, 0.04, 1, "ok", 0.48, 0.44, '[]'),
    ]
    conn = FakeConn(rows_desc)

    class FakeDb:
        def _connect(self):
            return conn

    monkeypatch.setattr(dbmod, "_db", lambda: FakeDb())
    monkeypatch.setattr(dbmod, "init_plant_tables", lambda c: None)

    out = dbmod.list_color_timeline("m1", limit=3)
    assert [s["sampled_at"] for s in out] == [100.0, 200.0, 300.0]
    assert "ORDER BY sampled_at DESC" in conn.last_sql
    assert out[-1]["greenness"] == 0.55
    assert out[-1]["G_norm"] == 1.1
    assert out[-1]["sample_ok"] is True
    assert out[-1]["L_frame"] == 0.52
    assert out[-1]["L_plant"] == 0.48


def test_sanitize_night_g_norm_on_read():
    from basebuddy.plugins.plant_health.db import _sanitize_night_g_norm

    g, ok, qual, flags = _sanitize_night_g_norm(
        12.0, 0.04, 0.05, True, "ok", []
    )
    assert g is None
    assert ok is False
    assert qual == "poor"
    assert "L_bg_low" in flags


def test_sanitize_desaturated_and_dim_on_read():
    from basebuddy.plugins.plant_health.db import _sanitize_night_g_norm

    g, ok, qual, flags = _sanitize_night_g_norm(
        0.82, 0.41, 0.46, True, "ok", [], plant_s_mean=0.0
    )
    assert g is None
    assert ok is False
    assert "plant_desaturated" in flags

    g2, ok2, qual2, flags2 = _sanitize_night_g_norm(
        0.87, 0.38, 0.39, True, "ok", [], plant_s_mean=70.0
    )
    assert g2 is None
    assert ok2 is True  # dim, not hard-poor
    assert "G_norm_dim_light" in flags2


def test_sample_is_good_rejects_dark_legacy():
    from basebuddy.plugins.plant_health.db import _sample_is_good

    assert _sample_is_good({"G_norm": 8.0, "L_bg": 0.03, "sample_ok": None}) is False
    assert _sample_is_good({"G_norm": 1.1, "L_bg": 0.5, "sample_ok": True}) is True
    assert _sample_is_good({
        "G_norm": 1.0, "L_bg": 0.5, "sample_ok": True,
        "quality_flags": ["L_frame_low"],
    }) is False
    assert _sample_is_good({
        "G_norm": 0.85, "L_bg": 0.38, "L_frame": 0.39, "sample_ok": True,
    }) is False
    assert _sample_is_good({
        "G_norm": 0.82, "L_bg": 0.45, "L_frame": 0.46, "sample_ok": True,
        "hsv": {"s": 0.0},
    }) is False
