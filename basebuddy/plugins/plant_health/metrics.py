"""Color / coverage metrics from plant segmentation masks."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def analyze_color_profile(image: np.ndarray, mask: np.ndarray) -> Optional[Dict[str, Any]]:
    """HSV + RGB statistics for pixels inside *mask*."""
    if mask is None or np.sum(mask > 0) == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masked_hsv = hsv[mask > 0]
    masked_bgr = image[mask > 0]
    if len(masked_hsv) == 0:
        return None

    h_mean, s_mean, v_mean = np.mean(masked_hsv, axis=0)
    h_std, s_std, v_std = np.std(masked_hsv, axis=0)
    b_mean, g_mean, r_mean = np.mean(masked_bgr, axis=0)

    percentiles = [10, 50, 90]
    dominant_colors = []
    for p in percentiles:
        b, g, r = np.percentile(masked_bgr, p, axis=0)
        dominant_colors.append([int(r), int(g), int(b)])

    plant_pixels = int(np.sum(mask > 0))
    total_pixels = mask.shape[0] * mask.shape[1]
    coverage = plant_pixels / total_pixels if total_pixels else 0.0

    y_coords, x_coords = np.where(mask > 0)
    centroid = (float(np.mean(x_coords)), float(np.mean(y_coords)))

    # Greenness index: useful for subtle trend detection
    rgb = masked_bgr.astype(np.float32)
    greenness = float(np.mean(rgb[:, 1] / (np.sum(rgb, axis=1) + 1e-6)))

    return {
        "coverage": round(coverage, 5),
        "plant_pixels": plant_pixels,
        "centroid_x": centroid[0],
        "centroid_y": centroid[1],
        "hsv_mean": {"h": round(float(h_mean), 2), "s": round(float(s_mean), 2), "v": round(float(v_mean), 2)},
        "hsv_std": {"h": round(float(h_std), 2), "s": round(float(s_std), 2), "v": round(float(v_std), 2)},
        "rgb_mean": {"r": round(float(r_mean), 1), "g": round(float(g_mean), 1), "b": round(float(b_mean), 1)},
        "dominant_colors_rgb": dominant_colors,
        "greenness": round(greenness, 4),
    }


def apply_color_filter(image: np.ndarray, color_profile: dict) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hsv_range = color_profile.get("hsv_range") or {}
    lower = np.array([
        hsv_range.get("h_min", 0),
        hsv_range.get("s_min", 0),
        hsv_range.get("v_min", 0),
    ])
    upper = np.array([
        hsv_range.get("h_max", 180),
        hsv_range.get("s_max", 255),
        hsv_range.get("v_max", 255),
    ])
    return cv2.inRange(hsv, lower, upper)


def roi_mask(shape: Tuple[int, int], roi: dict) -> np.ndarray:
    h, w = shape
    mask = np.zeros((h, w), dtype=np.uint8)
    x = float(roi.get("x", 0))
    y = float(roi.get("y", 0))
    rw = float(roi.get("w", 1))
    rh = float(roi.get("h", 1))
    if all(0 <= v <= 1 for v in (x, y, rw, rh)):
        x1, y1 = int(x * w), int(y * h)
        x2, y2 = int((x + rw) * w), int((y + rh) * h)
    else:
        x1, y1, x2, y2 = int(x), int(y), int(x + rw), int(y + rh)
    mask[max(0, y1):min(h, y2), max(0, x1):min(w, x2)] = 255
    return mask


def load_color_profile(camera_id: int, pattern_id: int = -1) -> Optional[dict]:
    """Load SAM+color pattern from plant segmentation configs."""
    from basebuddy.plugins.plant_health.segmentation import prompt_config_path

    path = prompt_config_path(camera_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        patterns = cfg.get("patterns") or []
        if not patterns:
            return None
        if pattern_id >= 0 and pattern_id < len(patterns):
            pat = patterns[pattern_id]
        else:
            pat = patterns[-1]
        return (pat.get("pattern") or {}).get("color_profile")
    except Exception:
        return None


def build_plant_mask(frame: np.ndarray, monitor: dict) -> np.ndarray:
    """Segment plant region: saved color profile → ROI → HSV green fallback."""
    seg = monitor.get("segmentation") or {}
    mode = seg.get("mode") or "auto"
    h, w = frame.shape[:2]

    if mode in ("color_profile", "auto"):
        cam = seg.get("pattern_camera_id", monitor.get("camera_id", 0))
        pid = int(seg.get("pattern_id", -1))
        profile = load_color_profile(int(cam), pid)
        if profile:
            mask = apply_color_filter(frame, profile)
            if np.sum(mask > 0) > 0:
                return mask

    roi = monitor.get("roi") or seg.get("roi")
    if roi:
        return roi_mask((h, w), roi)

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([25, 30, 30])
    upper = np.array([95, 255, 255])
    return cv2.inRange(hsv, lower, upper)


def extract_metrics(frame: np.ndarray, monitor: dict) -> Optional[Dict[str, Any]]:
    mask = build_plant_mask(frame, monitor)
    if np.sum(mask > 0) == 0:
        return None
    return analyze_color_profile(frame, mask)
