"""Occupancy detection via baseline image comparison (no GPU required)."""
from __future__ import annotations

import os
from typing import Tuple

import cv2
import numpy as np


def extract_roi(frame: np.ndarray, roi: dict) -> np.ndarray:
    h, w = frame.shape[:2]
    x1 = int(float(roi.get("x1", 0)) * w)
    y1 = int(float(roi.get("y1", 0)) * h)
    x2 = int(float(roi.get("x2", 1)) * w)
    y2 = int(float(roi.get("y2", 1)) * h)
    x1, x2 = max(0, min(w - 1, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h - 1, y1)), max(0, min(h, y2))
    if x2 <= x1 or y2 <= y1:
        return frame[0:0, 0:0]
    return frame[y1:y2, x1:x2]


def compare_occupancy(
    crop: np.ndarray,
    baseline_path: str,
    threshold: float = 25.0,
) -> Tuple[str, float]:
    if crop.size == 0:
        return "unknown", 0.0
    if not baseline_path or not os.path.isfile(baseline_path):
        return "unknown", 0.0

    baseline = cv2.imread(baseline_path)
    if baseline is None:
        return "unknown", 0.0

    crop_r = cv2.resize(crop, (64, 64))
    base_r = cv2.resize(baseline, (64, 64))
    diff = float(np.mean(cv2.absdiff(crop_r, base_r)))
    if diff > threshold:
        confidence = min(1.0, diff / 100.0)
        return "present", confidence
    confidence = min(1.0, 1.0 - diff / max(threshold, 1.0))
    return "empty", confidence


def detect_door_open(frame: np.ndarray, door_roi: dict, reference_brightness: float | None) -> Tuple[bool, float]:
    """Heuristic: large brightness shift in door ROI suggests door open."""
    crop = extract_roi(frame, door_roi)
    if crop.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    if reference_brightness is None:
        return False, brightness
    delta = abs(brightness - reference_brightness)
    return delta > 35.0, brightness
