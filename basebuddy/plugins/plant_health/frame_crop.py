"""Normalized frame crop for plant monitors (analysis + playback)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

MIN_FRAC = 0.04


def normalize_crop(crop: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Return {x,y,w,h} in 0–1, or None if the crop is missing/full-frame."""
    if not crop or not isinstance(crop, dict):
        return None
    try:
        x = float(crop.get("x", 0))
        y = float(crop.get("y", 0))
        w = float(crop.get("w", 0))
        h = float(crop.get("h", 0))
    except (TypeError, ValueError):
        return None
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(max(w, 0.0), 1.0 - x)
    h = min(max(h, 0.0), 1.0 - y)
    if w < MIN_FRAC or h < MIN_FRAC:
        return None
    if w >= 0.98 and h >= 0.98 and x <= 0.01 and y <= 0.01:
        return None
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


def pixel_rect(crop: Dict[str, float], width: int, height: int) -> Tuple[int, int, int, int]:
    x1 = int(round(crop["x"] * width))
    y1 = int(round(crop["y"] * height))
    x2 = int(round((crop["x"] + crop["w"]) * width))
    y2 = int(round((crop["y"] + crop["h"]) * height))
    x1 = min(max(0, x1), max(0, width - 8))
    y1 = min(max(0, y1), max(0, height - 8))
    x2 = min(width, max(x1 + 8, x2))
    y2 = min(height, max(y1 + 8, y2))
    return x1, y1, x2, y2


def apply_frame_crop(frame: Optional[np.ndarray], crop: Optional[Dict[str, Any]]) -> Optional[np.ndarray]:
    """Crop an image (or mask) with a normalized rect. Returns a copy."""
    if frame is None:
        return None
    norm = normalize_crop(crop)
    if not norm:
        return frame
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = pixel_rect(norm, w, h)
    return frame[y1:y2, x1:x2].copy()


def prepare_historical_frame(
    frame: Optional[np.ndarray],
    crop: Optional[Dict[str, Any]],
) -> Optional[np.ndarray]:
    """Crop a stored still if it still looks like a full camera frame.

    Reanalyzed stills that already match the crop aspect are left alone so a
    second pass does not slice the ROI twice.
    """
    if frame is None:
        return None
    norm = normalize_crop(crop)
    if not norm:
        return frame
    h, w = frame.shape[:2]
    if w < 8 or h < 8:
        return frame
    aspect = w / float(h)
    fullish = abs(aspect - (16.0 / 9.0)) < 0.18 or abs(aspect - (4.0 / 3.0)) < 0.12
    if not fullish:
        return frame
    return apply_frame_crop(frame, norm)


def points_into_crop(
    points: Sequence,
    src_w: int,
    src_h: int,
    crop: Dict[str, float],
    dst_w: int,
    dst_h: int,
) -> List[List[float]]:
    """Map full-frame pixels into a cropped frame."""
    out: List[List[float]] = []
    if not src_w or not src_h:
        return [[float(p[0]), float(p[1])] for p in points]
    for p in points:
        nx = float(p[0]) / float(src_w)
        ny = float(p[1]) / float(src_h)
        lx = (nx - crop["x"]) / max(1e-6, crop["w"])
        ly = (ny - crop["y"]) / max(1e-6, crop["h"])
        out.append([lx * dst_w, ly * dst_h])
    return out


def adapt_points_for_crop(
    points: Sequence,
    *,
    src_w: Optional[int],
    src_h: Optional[int],
    dst_w: int,
    dst_h: int,
    crop: Optional[Dict[str, Any]],
) -> List[List[float]]:
    """Scale series points onto the current (possibly cropped) frame."""
    if not points:
        return []
    norm = normalize_crop(crop)
    sw = int(src_w or 0)
    sh = int(src_h or 0)
    if norm and sw and sh and sw > dst_w * 1.12:
        return points_into_crop(points, sw, sh, norm, dst_w, dst_h)
    if not sw or not sh or sw <= 0 or sh <= 0:
        return [[float(p[0]), float(p[1])] for p in points]
    sx = dst_w / float(sw)
    sy = dst_h / float(sh)
    return [[float(p[0]) * sx, float(p[1]) * sy] for p in points]


def align_mask_to_frame(
    mask: Optional[np.ndarray],
    frame: np.ndarray,
    crop: Optional[Dict[str, Any]] = None,
) -> Optional[np.ndarray]:
    """Resize or crop a saved mask so it matches *frame*."""
    if mask is None or frame is None:
        return mask
    import cv2

    fh, fw = frame.shape[:2]
    mh, mw = mask.shape[:2]
    if mh == fh and mw == fw:
        return (mask > 0).astype(np.uint8) * 255
    norm = normalize_crop(crop)
    if norm and (mw > fw * 1.12 or mh > fh * 1.12):
        mask = apply_frame_crop(mask, norm)
        mh, mw = mask.shape[:2]
    if mh != fh or mw != fw:
        mask = cv2.resize(mask, (fw, fh), interpolation=cv2.INTER_NEAREST)
    return (mask > 0).astype(np.uint8) * 255
