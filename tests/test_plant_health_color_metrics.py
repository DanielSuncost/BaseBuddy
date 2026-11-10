"""Tests for plant health color metrics (G_rel / L_bg / G_norm / yellow / luminance)."""
from __future__ import annotations

import numpy as np
import pytest

from basebuddy.plugins.plant_health.metrics import analyze_color_profile


def _make_frame(h=60, w=80):
    """BGR frame: green plant region left, mid-gray background right."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # Background ~ mid gray (V ~ 0.5)
    img[:, :] = (128, 128, 128)
    # Green plant on left half
    img[:, : w // 2] = (40, 160, 40)  # BGR green
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, : w // 2] = 255
    return img, mask


def test_analyze_color_profile_core_fields():
    img, mask = _make_frame()
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["plant_pixels"] == 60 * 40
    # Foliage coverage ≈ green-in-mask / frame (left half is green)
    assert 0.4 < m["coverage"] < 0.6
    assert 0.4 < m["mask_coverage"] < 0.6
    assert m["green_pixels"] == m["plant_pixels"]
    assert m["greenness"] == pytest.approx(m["G_rel"], abs=1e-6)
    assert m["G_raw"] == pytest.approx(m["G_rel"], abs=1e-6)
    assert m["G_rel"] > 0.4  # green-dominant
    assert 0.3 < m["L_bg"] < 0.7
    assert 0.3 < m["L_frame"] < 0.7
    assert 0.3 < m["L_plant"] < 0.8
    assert m["G_norm"] is not None
    assert m["G_norm"] == pytest.approx(m["G_rel"] / m["L_bg"], rel=1e-3)
    assert m["yellow_fraction"] is not None
    assert 0.0 <= m["yellow_fraction"] <= 1.0
    assert m["sample_ok"] is True
    assert m["sample_quality"] == "ok"


def test_coverage_is_green_in_mask_not_raw_mask_area():
    """Static SAM masks must not freeze coverage — use foliage green fraction."""
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (128, 128, 128)
    # Only a quarter of the mask region is actually green
    img[:20, :10] = (40, 180, 40)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:20, :20] = 255  # 25% mask coverage
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["mask_coverage"] == pytest.approx(0.25, abs=0.01)
    # Green foliage ≈ 20*10 / 1600 = 0.125
    assert m["coverage"] == pytest.approx(0.125, abs=0.02)
    assert m["coverage"] < m["mask_coverage"]


def test_desaturated_grow_light_nulls_g_norm():
    """Bright gray/white plant pixels (grow light in mask) must not drive G_norm."""
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (100, 100, 100)  # mid bg — above hard dark gate
    img[:, :20] = (210, 210, 210)  # bright desaturated "plant"
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :20] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["hsv_mean"]["s"] < 12
    assert m["G_norm"] is None
    assert m["sample_ok"] is False
    assert "plant_desaturated" in m["quality_flags"]


def test_dim_light_nulls_g_norm_but_keeps_sample():
    """L≈0.35–0.40 (lights off but not pitch black) must not spike G_norm."""
    h, w = 40, 40
    # V ≈ 0.35 * 255 ≈ 89
    img = np.full((h, w, 3), 89, dtype=np.uint8)
    img[:, :20] = (50, 140, 50)  # chromatic green plant
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :20] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert 0.08 < m["L_bg"] < 0.42
    assert m["G_norm"] is None
    assert "G_norm_dim_light" in m["quality_flags"]
    # Not hard-poor — L_* panels / coverage still useful
    assert m["sample_ok"] is True


def test_luminance_fields_frame_vs_plant():
    """L_frame is full-frame mean; L_plant is mask-only; L_bg is outside mask."""
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (200, 200, 200)  # bright bg
    img[:, :20] = (20, 80, 20)  # darker green plant
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :20] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["L_plant"] < m["L_bg"]
    assert m["L_frame"] == pytest.approx((m["L_plant"] + m["L_bg"]) / 2.0, abs=0.05)


def test_yellow_fraction_on_senescent_pixels():
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (100, 100, 100)
    # Yellow-ish BGR (low B, mid G, high R) → OpenCV H in yellow band
    img[:20, :] = (30, 180, 200)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:20, :] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["yellow_fraction"] > 0.5


def test_poor_quality_when_background_dark():
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (5, 5, 5)  # nearly black bg
    img[:, :20] = (30, 120, 30)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :20] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["L_bg"] < 0.08
    assert m["sample_ok"] is False
    assert "L_bg_low" in m["quality_flags"]
    # Night spike guard: no G_norm when lighting is below the quality gate
    assert m["G_norm"] is None


def test_g_norm_none_when_l_bg_tiny():
    h, w = 20, 20
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :10] = (20, 100, 20)
    # bg stays 0
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :10] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["L_bg"] < 0.05
    assert m["G_norm"] is None
    assert m["sample_ok"] is False


def test_g_norm_none_for_borderline_dim_night():
    """L_bg just under the quality gate used to produce huge G_norm spikes."""
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # HSV V ≈ 0.06 * 255 ≈ 15 (between old eps 0.05 and gate 0.08)
    img[:, :] = (15, 15, 15)
    img[:, :20] = (25, 110, 25)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:, :20] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["L_bg"] < 0.08
    assert m["G_norm"] is None
    assert m["sample_ok"] is False
    assert "L_bg_low" in m["quality_flags"] or "L_frame_low" in m["quality_flags"]


def test_poor_quality_when_frame_dark():
    h, w = 30, 30
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (4, 4, 4)
    img[:15, :] = (6, 18, 6)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[:15, :] = 255
    m = analyze_color_profile(img, mask)
    assert m is not None
    assert m["L_frame"] < 0.08
    assert m["sample_ok"] is False
    assert "L_frame_low" in m["quality_flags"] or "L_bg_low" in m["quality_flags"]
    assert m["G_norm"] is None


def test_empty_mask_returns_none():
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    mask = np.zeros((10, 10), dtype=np.uint8)
    assert analyze_color_profile(img, mask) is None
