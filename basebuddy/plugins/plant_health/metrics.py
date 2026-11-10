"""Color / coverage metrics from plant segmentation masks."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# OpenCV HSV: H in [0, 180]. Yellow–brown senescence band (approx).
_YELLOW_H_MIN = 12
_YELLOW_H_MAX = 38
_YELLOW_S_MIN = 40
_YELLOW_V_MIN = 40

# Background / frame luminance quality gates (HSV V / 255).
# Night frames: L_bg → tiny while G_rel stays noisy → G_norm = G_rel/L_bg explodes.
# Keep the div-by-zero epsilon aligned with the "too dark" quality gate so we never
# store a huge ratio for frames we already mark as poor lighting.
_L_BG_LOW = 0.08
_L_BG_HIGH = 0.92
_L_BG_EPS = _L_BG_LOW  # no G_norm when lighting is unreliable
_L_FRAME_LOW = 0.08
_L_PLANT_LOW = 0.05
# Soft floor for G_norm only: dim / lights-off rooms can sit at L≈0.35–0.40 with
# elevated ratios while still above the hard "pitch black" gate.
_L_G_NORM_MIN = 0.42
_MIN_PLANT_PIXELS = 80
# Hard ceiling: even if L_bg squeaks past the gate, absurd ratios are not health signal.
_G_NORM_MAX = 5.0
# OpenCV HSV S mean below this → plant region is gray/white (grow light in mask).
_PLANT_S_MIN = 12.0
# Foliage green band inside mask (matches sam_growth); coverage uses this, not raw mask area.
_GREEN_H_MIN = 25
_GREEN_H_MAX = 95
_GREEN_S_MIN = 30
_GREEN_V_MIN = 30

# Flags that mean "do not trust G_norm" (UI + trends).
G_NORM_EXCLUDE_FLAGS = (
    "L_bg_low",
    "L_frame_low",
    "L_plant_low",
    "G_norm_clipped",
    "G_norm_dim_light",
    "plant_desaturated",
)


def _sample_quality(
    L_bg: float,
    plant_pixels: int,
    coverage: float,
    *,
    L_frame: Optional[float] = None,
    L_plant: Optional[float] = None,
    plant_s_mean: Optional[float] = None,
    mask_coverage: Optional[float] = None,
) -> Dict[str, Any]:
    """Derive a sample-quality flag from lighting / coverage heuristics."""
    flags: List[str] = []
    if L_bg < _L_BG_LOW:
        flags.append("L_bg_low")
    if L_bg > _L_BG_HIGH:
        flags.append("L_bg_high")
    if L_frame is not None and L_frame < _L_FRAME_LOW:
        flags.append("L_frame_low")
    if L_plant is not None and L_plant < _L_PLANT_LOW:
        flags.append("L_plant_low")
    if plant_s_mean is not None and plant_s_mean < _PLANT_S_MIN:
        flags.append("plant_desaturated")
    if plant_pixels < _MIN_PLANT_PIXELS:
        flags.append("few_pixels")
    if coverage is not None and coverage < 0.001:
        flags.append("low_coverage")
    if (
        mask_coverage is not None
        and mask_coverage > 0.02
        and coverage is not None
        and coverage < 0.002
    ):
        flags.append("low_green_in_mask")
    # Hard failures → sample_ok false. Dim-light G_norm gate is applied separately.
    hard = {
        "L_bg_low",
        "L_frame_low",
        "L_plant_low",
        "plant_desaturated",
        "few_pixels",
        "low_coverage",
    }
    ok = not any(f in hard for f in flags)
    return {
        "sample_ok": ok,
        "quality_flags": flags,
        "sample_quality": "ok" if ok else "poor",
    }


def analyze_color_profile(image: np.ndarray, mask: np.ndarray) -> Optional[Dict[str, Any]]:
    """HSV + RGB statistics for pixels inside *mask*, plus lighting-normalized fields.

    New fields (v1 health tranche):
      G_raw       — legacy greenness alias (mean G/(R+G+B) on plant pixels)
      G_rel       — relative greenness G/(R+G+B) on plant pixels
      L_bg        — mean HSV V of non-plant pixels (0–1)
      L_frame     — mean HSV V of the full frame (0–1)
      L_plant     — mean HSV V inside the plant/SAM mask (0–1)
      G_norm      — G_rel / L_bg (null when lighting unreliable / dim / desaturated)
      yellow_fraction — share of plant pixels in yellow–brown hue band
      coverage    — green foliage pixels inside mask / frame (not raw mask area)
      mask_coverage — raw mask pixels / frame (constant under a static SAM bitmap)
      sample_ok / sample_quality / quality_flags
    """
    if mask is None or np.sum(mask > 0) == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    plant = mask > 0
    masked_hsv = hsv[plant]
    masked_bgr = image[plant]
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

    plant_pixels = int(np.sum(plant))
    total_pixels = mask.shape[0] * mask.shape[1]
    mask_coverage = plant_pixels / total_pixels if total_pixels else 0.0

    # Foliage coverage = green pixels inside mask / frame.
    # Raw mask_coverage is constant under a static SAM bitmap; green-in-mask tracks
    # visible foliage and collapses under grow lights / night IR.
    green_bin = cv2.inRange(
        hsv,
        np.array([_GREEN_H_MIN, _GREEN_S_MIN, _GREEN_V_MIN], dtype=np.uint8),
        np.array([_GREEN_H_MAX, 255, 255], dtype=np.uint8),
    )
    green_pixels = int(np.sum((green_bin > 0) & plant))
    coverage = green_pixels / total_pixels if total_pixels else 0.0

    y_coords, x_coords = np.where(plant)
    centroid = (float(np.mean(x_coords)), float(np.mean(y_coords)))

    # Relative greenness (legacy column name: greenness)
    rgb = masked_bgr.astype(np.float32)
    G_rel = float(np.mean(rgb[:, 1] / (np.sum(rgb, axis=1) + 1e-6)))
    greenness = G_rel  # backward-compatible stored field
    G_raw = G_rel  # existing greenness renamed for new API consumers

    # Luminance channels (HSV V / 255)
    L_frame = float(np.mean(hsv[:, :, 2]) / 255.0)
    L_plant = float(np.mean(masked_hsv[:, 2]) / 255.0)
    bg = ~plant
    bg_count = int(np.sum(bg))
    if bg_count > 0:
        L_bg = float(np.mean(hsv[bg][:, 2]) / 255.0)
    else:
        # Full-frame plant — fall back to global V
        L_bg = L_frame

    quality = _sample_quality(
        L_bg,
        plant_pixels,
        coverage,
        L_frame=L_frame,
        L_plant=L_plant,
        plant_s_mean=float(s_mean),
        mask_coverage=mask_coverage,
    )
    lighting_bad = any(
        f in quality["quality_flags"]
        for f in ("L_bg_low", "L_frame_low", "L_plant_low", "plant_desaturated")
    )
    dim_for_gnorm = (L_bg < _L_G_NORM_MIN) or (
        L_frame is not None and L_frame < _L_G_NORM_MIN
    )

    # Skip G_norm entirely in low / dim / desaturated light — never store a night spike.
    if (not lighting_bad) and (not dim_for_gnorm) and L_bg >= _L_BG_EPS:
        G_norm = float(G_rel / L_bg)
        if G_norm > _G_NORM_MAX:
            G_norm = None
            quality["quality_flags"] = list(quality["quality_flags"]) + ["G_norm_clipped"]
            quality["sample_ok"] = False
            quality["sample_quality"] = "poor"
    else:
        G_norm = None
        if dim_for_gnorm and not lighting_bad:
            if "G_norm_dim_light" not in quality["quality_flags"]:
                quality["quality_flags"] = list(quality["quality_flags"]) + [
                    "G_norm_dim_light"
                ]

    # Yellow–brown senescence proxy on plant pixels
    h = masked_hsv[:, 0].astype(np.float32)
    s = masked_hsv[:, 1].astype(np.float32)
    v = masked_hsv[:, 2].astype(np.float32)
    yellow = (
        (h >= _YELLOW_H_MIN)
        & (h <= _YELLOW_H_MAX)
        & (s >= _YELLOW_S_MIN)
        & (v >= _YELLOW_V_MIN)
    )
    yellow_fraction = float(np.sum(yellow) / max(1, plant_pixels))

    return {
        "coverage": round(coverage, 5),
        "mask_coverage": round(mask_coverage, 5),
        "plant_pixels": plant_pixels,
        "green_pixels": green_pixels,
        "centroid_x": centroid[0],
        "centroid_y": centroid[1],
        "hsv_mean": {"h": round(float(h_mean), 2), "s": round(float(s_mean), 2), "v": round(float(v_mean), 2)},
        "hsv_std": {"h": round(float(h_std), 2), "s": round(float(s_std), 2), "v": round(float(v_std), 2)},
        "rgb_mean": {"r": round(float(r_mean), 1), "g": round(float(g_mean), 1), "b": round(float(b_mean), 1)},
        "dominant_colors_rgb": dominant_colors,
        "greenness": round(greenness, 4),
        "G_raw": round(G_raw, 4),
        "G_rel": round(G_rel, 4),
        "L_bg": round(L_bg, 4),
        "L_frame": round(L_frame, 4),
        "L_plant": round(L_plant, 4),
        "G_norm": round(G_norm, 4) if G_norm is not None else None,
        "yellow_fraction": round(yellow_fraction, 4),
        "sample_ok": quality["sample_ok"],
        "sample_quality": quality["sample_quality"],
        "quality_flags": quality["quality_flags"],
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


def load_pattern_entry(camera_id: int, pattern_id: int = -1) -> Optional[dict]:
    """Load a saved SAM pattern entry (including color_profile / mask_path)."""
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
            return patterns[pattern_id]
        return patterns[-1]
    except Exception:
        return None


def load_color_profile(camera_id: int, pattern_id: int = -1) -> Optional[dict]:
    """Load SAM+color pattern from plant segmentation configs."""
    pat = load_pattern_entry(camera_id, pattern_id)
    if not pat:
        return None
    return (pat.get("pattern") or {}).get("color_profile")


def resolve_mask_path(monitor: dict) -> Optional[str]:
    """Return filesystem path to the persisted SAM mask for this monitor, if any."""
    seg = monitor.get("segmentation") or {}
    direct = seg.get("mask_path")
    if direct and os.path.isfile(direct):
        return direct

    cam = seg.get("pattern_camera_id", monitor.get("camera_id", 0))
    pid = int(seg.get("pattern_id", -1))
    pat = load_pattern_entry(int(cam), pid)
    if not pat:
        return None
    for candidate in (
        pat.get("mask_path"),
        (pat.get("pattern") or {}).get("mask_path"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def load_saved_mask(frame_shape: Tuple[int, int], monitor: dict) -> Optional[np.ndarray]:
    """Load persisted SAM bitmap resized to *frame_shape* (h, w)."""
    path = resolve_mask_path(monitor)
    if not path:
        return None
    mask = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if mask is None or np.sum(mask > 0) == 0:
        return None
    h, w = frame_shape[:2]
    from basebuddy.plugins.plant_health.frame_crop import align_mask_to_frame

    dummy = np.zeros((h, w, 3), dtype=np.uint8)
    return align_mask_to_frame(mask, dummy, monitor.get("frame_crop"))


def _green_refine_threshold(monitor: dict, override=None) -> float:
    if override is not None:
        from basebuddy.plugins.plant_health.green_refine import clamp_threshold

        return clamp_threshold(override)
    raw = (monitor or {}).get("sam_growth") or {}
    from basebuddy.plugins.plant_health.green_refine import clamp_threshold

    return clamp_threshold(raw.get("green_refine", 0))


def _luma_carve_threshold(monitor: dict, override=None) -> float:
    from basebuddy.plugins.plant_health.green_refine import clamp_luma

    if override is not None:
        return clamp_luma(override)
    raw = (monitor or {}).get("sam_growth") or {}
    return clamp_luma(raw.get("luma_carve", 0))


def build_plant_mask(
    frame: np.ndarray,
    monitor: dict,
    *,
    green_refine=None,
    luma_carve=None,
) -> np.ndarray:
    """Segment plant region: saved SAM bitmap → color profile → ROI → HSV green."""
    seg = monitor.get("segmentation") or {}
    mode = seg.get("mode") or "auto"
    h, w = frame.shape[:2]

    mask = None
    # Preferred: exact region the user saved in the SAM editor.
    if mode in ("color_profile", "auto", "saved_mask"):
        saved = load_saved_mask((h, w), monitor)
        if saved is not None and np.sum(saved > 0) > 0:
            mask = saved

    if mask is None and mode in ("color_profile", "auto"):
        cam = seg.get("pattern_camera_id", monitor.get("camera_id", 0))
        pid = int(seg.get("pattern_id", -1))
        profile = load_color_profile(int(cam), pid)
        if profile:
            filtered = apply_color_filter(frame, profile)
            if np.sum(filtered > 0) > 0:
                mask = filtered

    if mask is None:
        roi = monitor.get("roi") or seg.get("roi")
        if roi:
            mask = roi_mask((h, w), roi)

    if mask is None:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower = np.array([25, 30, 30])
        upper = np.array([95, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)

    from basebuddy.plugins.plant_health.green_refine import refine_mask

    mask = refine_mask(
        frame,
        mask,
        _green_refine_threshold(monitor, green_refine),
        _luma_carve_threshold(monitor, luma_carve),
    )
    return mask


def extract_metrics(frame: np.ndarray, monitor: dict) -> Optional[Dict[str, Any]]:
    mask = build_plant_mask(frame, monitor)
    if np.sum(mask > 0) == 0:
        return None
    return analyze_color_profile(frame, mask)


def render_masked_frame_jpeg(
    frame: np.ndarray,
    monitor: dict,
    *,
    quality: int = 88,
    green_refine=None,
    luma_carve=None,
) -> Tuple[Optional[bytes], Optional[Dict[str, Any]], Optional[str]]:
    """Composite live frame with plant-mask tint for UI preview.

    Returns (jpeg_bytes, metrics_or_none, error_or_none).
    """
    if frame is None:
        return None, None, "No frame"
    mask = build_plant_mask(
        frame, monitor, green_refine=green_refine, luma_carve=luma_carve
    )
    display = frame.copy()
    has_mask = bool(np.sum(mask > 0) > 0)
    if has_mask:
        tint = display.copy()
        sel = mask > 0
        # BGR emerald tint matching SAM preview
        tint[sel] = (tint[sel] * 0.45 + np.array([30, 185, 130]) * 0.55).astype(np.uint8)
        display = tint
        # Soft outline so the region reads clearly on busy backgrounds
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(display, contours, -1, (40, 220, 140), 2)
    metrics = analyze_color_profile(frame, mask) if has_mask else None
    ok, buf = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return None, metrics, "Encode failed"
    return buf.tobytes(), metrics, None
