"""Normalized frame crop for plant analysis / playback."""
from __future__ import annotations

import numpy as np

from basebuddy.plugins.plant_health.frame_crop import (
    adapt_points_for_crop,
    align_mask_to_frame,
    apply_frame_crop,
    normalize_crop,
    points_into_crop,
)


def test_normalize_crop_rejects_tiny_and_full():
    assert normalize_crop(None) is None
    assert normalize_crop({"x": 0, "y": 0, "w": 1, "h": 1}) is None
    assert normalize_crop({"x": 0.1, "y": 0.1, "w": 0.02, "h": 0.5}) is None
    n = normalize_crop({"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.6})
    assert n == {"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.6}


def test_apply_frame_crop_cuts_pixels():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    img[20:80, 40:140] = 200
    out = apply_frame_crop(img, {"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.6})
    assert out.shape[0] == 60
    assert out.shape[1] == 100
    assert int(out.mean()) > 150


def test_points_into_crop_and_adapt():
    crop = {"x": 0.25, "y": 0.0, "w": 0.5, "h": 1.0}
    pts = points_into_crop([[80, 20]], 160, 80, crop, 80, 80)
    assert abs(pts[0][0] - 40) < 1.5
    adapted = adapt_points_for_crop(
        [[80, 20]], src_w=160, src_h=80, dst_w=80, dst_h=80, crop=crop
    )
    assert abs(adapted[0][0] - 40) < 1.5
    same = adapt_points_for_crop(
        [[40, 20]], src_w=80, src_h=80, dst_w=80, dst_h=80, crop=crop
    )
    assert abs(same[0][0] - 40) < 1e-6


def test_prepare_historical_frame_crops_wide_stills():
    from basebuddy.plugins.plant_health.frame_crop import prepare_historical_frame

    full = np.zeros((90, 160, 3), dtype=np.uint8)
    full[:, 80:] = 200
    crop = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}
    out = prepare_historical_frame(full, crop)
    assert out.shape[1] == 80
    already = np.zeros((90, 80, 3), dtype=np.uint8)
    same = prepare_historical_frame(already, crop)
    assert same.shape == already.shape


def test_align_mask_crops_then_resizes():
    mask = np.zeros((80, 160), dtype=np.uint8)
    mask[10:70, 80:150] = 255
    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    crop = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}
    out = align_mask_to_frame(mask, frame, crop)
    assert out.shape == (40, 40)
    assert int(np.sum(out > 0)) > 100
