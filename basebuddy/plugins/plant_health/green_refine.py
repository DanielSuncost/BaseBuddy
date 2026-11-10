"""Carve non-green pixels out of a SAM (or saved) plant mask.

SAM returns a filled silhouette that includes shelf, pot, and gaps between
leaves. A per-pixel excess-green score punches those holes so the working
mask tracks foliage instead of the convex blob.

Score is normalized ExG = (2G − R − B) / (R+G+B), scaled to 0–100.
Gray / white background sits near 0; green leaves typically 18–50.
Threshold 0 leaves the SAM mask unchanged.
"""
from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

DEFAULT_THRESHOLD = 0
MAX_THRESHOLD = 100


def clamp_threshold(value) -> float:
    try:
        t = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(float(MAX_THRESHOLD), t)), 2)


def clamp_luma(value) -> float:
    """Max HSV-V (0–100) to keep. 0 = off."""
    try:
        t = float(value)
    except (TypeError, ValueError):
        return 0.0
    if t <= 0:
        return 0.0
    return round(max(0.0, min(100.0, t)), 1)


def green_score_map(frame_bgr: np.ndarray) -> np.ndarray:
    """Per-pixel greenness in 0–100 (normalized excess green)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return np.zeros((1, 1), dtype=np.float32)
    b, g, r = cv2.split(frame_bgr.astype(np.float32))
    denom = r + g + b + 1.0
    nexg = (2.0 * g - r - b) / denom
    return np.clip(nexg * 100.0, 0.0, 100.0).astype(np.float32)


def refine_mask_by_green(
    frame_bgr: np.ndarray,
    mask: np.ndarray,
    threshold: float,
    *,
    morph: bool = True,
) -> np.ndarray:
    """Keep SAM pixels whose excess-green score is at least *threshold*.

    ``threshold <= 0`` returns the original binary mask (0/255).
    """
    if mask is None:
        return mask
    plant = (mask > 0).astype(np.uint8) * 255
    thr = clamp_threshold(threshold)
    if thr <= 0 or frame_bgr is None or frame_bgr.size == 0:
        return plant

    h, w = plant.shape[:2]
    if frame_bgr.shape[0] != h or frame_bgr.shape[1] != w:
        frame_bgr = cv2.resize(frame_bgr, (w, h), interpolation=cv2.INTER_AREA)

    # IR / lights-out wash: SAM region is gray. Carving would empty the mask.
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    plant_bool = plant > 0
    if int(np.sum(plant_bool)) > 0 and float(np.mean(hsv[:, :, 1][plant_bool])) < 12.0:
        return plant

    score = green_score_map(frame_bgr)
    keep = plant_bool & (score >= thr)
    out = keep.astype(np.uint8) * 255
    # 3×3 open eats thin leaflets; only use it on coarser carves.
    if morph and thr >= 2.0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        out = cv2.morphologyEx(out, cv2.MORPH_OPEN, kernel)
    return out


def refine_mask_by_luma(
    frame_bgr: np.ndarray,
    mask: np.ndarray,
    luma_max: float,
) -> np.ndarray:
    """Keep SAM pixels whose HSV V is at most *luma_max* (0–100). 0 = off.

    Drops blown highlights / white shelf while leaving darker foliage.
    """
    if mask is None:
        return mask
    plant = (mask > 0).astype(np.uint8) * 255
    cap = clamp_luma(luma_max)
    if cap <= 0 or frame_bgr is None or frame_bgr.size == 0:
        return plant

    h, w = plant.shape[:2]
    if frame_bgr.shape[0] != h or frame_bgr.shape[1] != w:
        frame_bgr = cv2.resize(frame_bgr, (w, h), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    plant_bool = plant > 0
    if int(np.sum(plant_bool)) > 0 and float(np.mean(hsv[:, :, 1][plant_bool])) < 12.0:
        return plant

    v100 = hsv[:, :, 2].astype(np.float32) * (100.0 / 255.0)
    keep = plant_bool & (v100 <= cap)
    out = keep.astype(np.uint8) * 255
    if int(np.sum(out > 0)) == 0:
        return plant
    return out


def clip_to_template(
    carved: np.ndarray,
    template: Optional[np.ndarray],
    bound_px: int = 1,
) -> np.ndarray:
    """Never add pixels outside the saved silhouette.

    Carves may punch holes; they must not grow the outline from frame to frame.
    """
    if carved is None:
        return carved
    plant = (carved > 0).astype(np.uint8)
    if template is None:
        return (plant * 255).astype(np.uint8)
    bound = (template > 0).astype(np.uint8)
    if bound.shape != plant.shape:
        bound = cv2.resize(
            bound, (plant.shape[1], plant.shape[0]), interpolation=cv2.INTER_NEAREST
        )
    pad = max(0, int(bound_px))
    if pad > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pad * 2 + 1, pad * 2 + 1))
        bound = cv2.dilate(bound, k)
    return ((plant & bound) * 255).astype(np.uint8)


def refine_mask(
    frame_bgr: np.ndarray,
    mask: np.ndarray,
    green_threshold: float = 0,
    luma_max: float = 0,
    template: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Apply green carve, then keep-only-darker luma carve, clipped to *template*."""
    src = mask
    out = refine_mask_by_green(frame_bgr, mask, green_threshold)
    out = refine_mask_by_luma(frame_bgr, out, luma_max)
    return clip_to_template(out, template if template is not None else src)


def refine_stats(
    frame_bgr: np.ndarray,
    sam_mask: np.ndarray,
    refined: np.ndarray,
) -> Tuple[int, int, float]:
    sam_px = int(np.sum(sam_mask > 0))
    ref_px = int(np.sum(refined > 0))
    carved = 1.0 - (ref_px / sam_px) if sam_px else 0.0
    return sam_px, ref_px, carved
