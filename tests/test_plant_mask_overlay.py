"""Plant health mask: saved SAM bitmap is preferred over HSV rebuild."""
from __future__ import annotations

import os

import cv2
import numpy as np

from basebuddy.plugins.plant_health.metrics import (
    apply_color_filter,
    build_plant_mask,
    load_saved_mask,
    resolve_mask_path,
)


def test_build_plant_mask_prefers_saved_bitmap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Frame with two green blobs; HSV would catch both, saved mask only left.
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    frame[10:40, 10:40] = (40, 180, 40)   # BGR green left
    frame[10:40, 70:100] = (40, 180, 40)  # BGR green right

    mask = np.zeros((80, 120), dtype=np.uint8)
    mask[10:40, 10:40] = 255
    mask_path = os.path.join("stills", "camera_0", "masks", "plant_mask_test.png")
    os.makedirs(os.path.dirname(mask_path), exist_ok=True)
    cv2.imwrite(mask_path, mask)

    monitor = {
        "camera_id": 0,
        "segmentation": {
            "mode": "color_profile",
            "pattern_id": 0,
            "pattern_camera_id": 0,
            "mask_path": mask_path,
        },
    }

    out = build_plant_mask(frame, monitor)
    assert out.shape == (80, 120)
    assert np.sum(out[10:40, 10:40] > 0) > 0
    assert np.sum(out[10:40, 70:100] > 0) == 0


def test_build_plant_mask_resizes_saved_bitmap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    mask = np.zeros((40, 60), dtype=np.uint8)
    mask[5:20, 5:25] = 255
    mask_path = "mask_small.png"
    cv2.imwrite(mask_path, mask)

    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    monitor = {
        "camera_id": 0,
        "segmentation": {"mode": "color_profile", "mask_path": mask_path},
    }
    out = build_plant_mask(frame, monitor)
    assert out.shape == (80, 120)
    assert np.sum(out > 0) > 0
    # Resized region should sit in the upper-left quadrant, not full frame.
    assert np.sum(out[40:, :] > 0) == 0
    assert np.sum(out[:, 60:] > 0) == 0


def test_hsv_fallback_when_no_saved_mask():
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    frame[10:40, 10:40] = (40, 200, 40)
    profile = {
        "hsv_range": {
            "h_min": 25, "h_max": 95,
            "s_min": 30, "s_max": 255,
            "v_min": 30, "v_max": 255,
        }
    }
    color_mask = apply_color_filter(frame, profile)
    assert np.sum(color_mask > 0) > 0

    monitor = {
        "camera_id": 0,
        "segmentation": {"mode": "auto"},
    }
    # No mask_path and no pattern file → green HSV fallback
    out = build_plant_mask(frame, monitor)
    assert np.sum(out[10:40, 10:40] > 0) > 0


def test_resolve_mask_path_missing():
    assert resolve_mask_path({"segmentation": {}}) is None
    assert load_saved_mask((10, 10), {"segmentation": {"mask_path": "nope.png"}}) is None
