"""Live-camera SAM segmentation for plant monitors."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def camera_key(camera_id: int) -> str:
    return f"camera_{int(camera_id)}"


def prompt_config_path(camera_id: int) -> str:
    return os.path.join("sam_prompt_configs", f"{camera_key(camera_id)}_prompts.json")


def capture_frame_bgr(
    camera_id: int,
    max_width: int = 1280,
    crop: Optional[Dict[str, Any]] = None,
) -> Optional[np.ndarray]:
    import basebuddy.modules.state as shared_state

    from basebuddy.plugins.plant_health.frame_crop import apply_frame_crop

    grabber = shared_state.grabbers.get(int(camera_id))
    if grabber is None:
        return None
    frame, _ts = grabber.get_latest_frame()
    if frame is None:
        return None
    if crop:
        frame = apply_frame_crop(frame, crop)
    if max_width and frame.shape[1] > max_width:
        scale = max_width / float(frame.shape[1])
        frame = cv2.resize(
            frame,
            (max_width, max(1, int(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return frame


def capture_frame_for_monitor(
    monitor: Optional[dict],
    max_width: int = 1280,
    *,
    crop: Optional[Dict[str, Any]] = None,
) -> Optional[np.ndarray]:
    if not monitor:
        return None
    used = crop if crop is not None else monitor.get("frame_crop")
    return capture_frame_bgr(
        int(monitor.get("camera_id", 0)),
        max_width=max_width,
        crop=used,
    )


def _save_reference_still(camera_id: int, frame: np.ndarray) -> str:
    """Save frame under stills/ for color-profile analysis."""
    from basebuddy.core.upload_safety import safe_basename

    cam = camera_key(camera_id)
    out_dir = os.path.join("stills", cam)
    os.makedirs(out_dir, exist_ok=True)
    fname = safe_basename(f"plant_ref_{int(time.time())}.jpg") or f"plant_ref_{int(time.time())}.jpg"
    path = os.path.join(out_dir, fname)
    cv2.imwrite(path, frame)
    return path


def _save_mask_bitmap(camera_id: int, mask: np.ndarray) -> str:
    """Persist the SAM binary mask used as the plant-region source of truth."""
    from basebuddy.core.upload_safety import safe_basename

    cam = camera_key(camera_id)
    out_dir = os.path.join("stills", cam, "masks")
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time())
    fname = safe_basename(f"plant_mask_{ts}.png") or f"plant_mask_{ts}.png"
    path = os.path.join(out_dir, fname)
    binary = (mask > 0).astype(np.uint8) * 255
    cv2.imwrite(path, binary)
    return path


_sam_preview_cache: Dict[str, Any] = {}


def _preview_cache_key(camera_id: int, points: List, labels: List, shape, crop=None) -> tuple:
    pts = tuple((round(float(p[0]), 1), round(float(p[1]), 1), int(l)) for p, l in zip(points, labels))
    c = None
    if crop:
        c = (
            round(float(crop.get("x", 0)), 4),
            round(float(crop.get("y", 0)), 4),
            round(float(crop.get("w", 0)), 4),
            round(float(crop.get("h", 0)), 4),
        )
    return (int(camera_id), pts, int(shape[0]), int(shape[1]), c)


def run_sam_mask(
    frame: np.ndarray,
    points: List,
    labels: List,
    *,
    cache_key: Optional[tuple] = None,
) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Run SAM once and return a uint8 0/255 mask (or error)."""
    if frame is None:
        return None, "No frame from camera"
    fg = [p for p, l in zip(points, labels) if l == 1]
    if not fg:
        return None, "Add at least one point on the plant (left-click)"

    if cache_key and _sam_preview_cache.get("key") == cache_key:
        cached = _sam_preview_cache.get("mask")
        if cached is not None and cached.shape[:2] == frame.shape[:2]:
            return cached.copy(), None

    from basebuddy.routes.plant_tracking import get_sam_predictor

    predictor = get_sam_predictor()
    if predictor is None:
        return None, (
            "SAM unavailable — install segment-anything and place "
            "sam_vit_b_01ec64.pth in the project root (see models/README.md)"
        )

    predictor.set_image(frame)
    point_coords = np.array(points, dtype=np.float32)
    point_labels = np.array(labels, dtype=np.int32)
    masks, _, _ = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=False,
    )
    mask = (masks[0] * 255).astype(np.uint8)
    if cache_key:
        _sam_preview_cache["key"] = cache_key
        _sam_preview_cache["mask"] = mask
    return mask, None


def preview_mask_png(camera_id: int, points: List, labels: List) -> Tuple[Optional[bytes], Optional[str]]:
    """Return PNG overlay bytes for live preview."""
    frame = capture_frame_bgr(camera_id)
    if frame is None:
        return None, "No frame from camera"

    if not points:
        return None, "Add at least one point"

    from basebuddy.routes.plant_tracking import get_sam_predictor

    predictor = get_sam_predictor()
    if predictor is None:
        return None, "SAM not available — install segment-anything model"

    predictor.set_image(frame)
    point_coords = np.array(points, dtype=np.float32)
    point_labels = np.array(labels, dtype=np.int32)
    masks, scores, _ = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=False,
    )
    mask = (masks[0] * 255).astype(np.uint8)
    overlay = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
    overlay[mask > 0] = [16, 185, 129, 160]

    ok, buf = cv2.imencode(".png", overlay)
    if not ok:
        return None, "Encode failed"
    return buf.tobytes(), None


def render_preview_with_points(
    camera_id: int,
    points: List,
    labels: List,
    *,
    green_refine: float = 0,
    luma_carve: float = 0,
    crop: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Composite base frame + mask + point markers as JPEG."""
    frame = capture_frame_bgr(camera_id, crop=crop)
    if frame is None:
        return None, "No frame from camera"

    display = frame.copy()
    if points:
        cache_key = _preview_cache_key(camera_id, points, labels, frame.shape, crop)
        mask, err = run_sam_mask(frame, points, labels, cache_key=cache_key)
        if err or mask is None:
            return None, err or "SAM failed"
        if green_refine or luma_carve:
            from basebuddy.plugins.plant_health.green_refine import refine_mask

            mask = refine_mask(frame, mask, green_refine, luma_carve)
        tint = display.copy()
        sel = mask > 0
        tint[sel] = (tint[sel] * 0.45 + np.array([30, 185, 130]) * 0.55).astype(np.uint8)
        display = tint

    for (x, y), lab in zip(points, labels):
        color = (40, 200, 80) if lab == 1 else (60, 60, 220)
        cv2.circle(display, (int(x), int(y)), 7, color, -1)
        cv2.circle(display, (int(x), int(y)), 8, (255, 255, 255), 2)

    ok, buf = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return None, "Encode failed"
    return buf.tobytes(), None


def save_segmentation_pattern(
    monitor_id: str,
    camera_id: int,
    points: List,
    labels: List,
    *,
    force_new_series: bool = False,
    frame_crop: Optional[Dict[str, Any]] = None,
    reanalyze: bool = False,
) -> Dict[str, Any]:
    from basebuddy.routes.plant_tracking.segmentation import analyze_color_profile as extract_hsv_profile
    from basebuddy.plugins.plant_health.config import get_monitor, update_monitor
    from basebuddy.plugins.plant_health.frame_crop import normalize_crop

    existing = get_monitor(monitor_id)
    if not existing:
        return {"ok": False, "error": "Monitor not found"}

    crop = normalize_crop(frame_crop)

    fg = [p for p, l in zip(points, labels) if l == 1]
    if not fg:
        return {"ok": False, "error": "Add at least one point on the plant (left-click)"}

    frame = capture_frame_bgr(camera_id, crop=crop)
    if frame is None:
        return {"ok": False, "error": "No frame from camera"}

    mask, err = run_sam_mask(frame, points, labels)
    if err or mask is None:
        return {"ok": False, "error": err or "SAM failed"}

    still_path = _save_reference_still(camera_id, frame)
    mask_path = _save_mask_bitmap(camera_id, mask)

    h, w = frame.shape[:2]
    fg_array = np.array(fg, dtype=np.float64)
    bg = [p for p, l in zip(points, labels) if l == 0]
    min_x, min_y = fg_array.min(axis=0)
    max_x, max_y = fg_array.max(axis=0)
    center_x, center_y = fg_array.mean(axis=0)

    pattern: Dict[str, Any] = {
        "bbox": {
            "min_x": float(min_x / w),
            "max_x": float(max_x / w),
            "min_y": float(min_y / h),
            "max_y": float(max_y / h),
        },
        "center": {
            "x": float(center_x / w),
            "y": float(center_y / h),
        },
        "fg_points_relative": [[float(x / w), float(y / h)] for x, y in fg],
        "bg_points_relative": [[float(x / w), float(y / h)] for x, y in bg],
        "mask_path": mask_path,
        "mask_shape": [int(h), int(w)],
    }
    color_profile = extract_hsv_profile(frame, mask)
    if color_profile:
        pattern["color_profile"] = color_profile

    os.makedirs("sam_prompt_configs", exist_ok=True)
    cfg_path = prompt_config_path(camera_id)
    if os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    else:
        cfg = {"camera_id": camera_key(camera_id), "patterns": []}

    cfg.setdefault("patterns", []).append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reference_image": still_path,
        "mask_path": mask_path,
        "pattern": pattern,
    })
    pattern_id = len(cfg["patterns"]) - 1

    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)

    bbox = pattern.get("bbox") or {}
    roi = None
    if bbox:
        roi = {
            "x": bbox.get("min_x", 0),
            "y": bbox.get("min_y", 0),
            "w": max(0.01, float(bbox.get("max_x", 1)) - float(bbox.get("min_x", 0))),
            "h": max(0.01, float(bbox.get("max_y", 1)) - float(bbox.get("min_y", 0))),
        }

    update_monitor(monitor_id, {
        "segmentation": {
            "mode": "color_profile",
            "pattern_id": pattern_id,
            "pattern_camera_id": int(camera_id),
            "mask_path": mask_path,
            # Absolute SAM anchors for growth bootstrap / re-segmentation.
            "sam_points": [[float(p[0]), float(p[1])] for p in points],
            "sam_labels": [int(x) for x in labels],
            "frame_w": int(w),
            "frame_h": int(h),
        },
        "roi": roi,
        "frame_crop": crop,
    })

    # Update the active growth series in place (or start one if none exists).
    growth_series = None
    try:
        from basebuddy.plugins.plant_health.sam_growth import create_series_from_save

        growth_series = create_series_from_save(
            monitor_id,
            int(camera_id),
            points,
            labels,
            frame_w=int(w),
            frame_h=int(h),
            initial_mask_path=mask_path,
            take_first_sample=True,
            frame=frame,
            mask=mask,
            force_new=bool(force_new_series or reanalyze),
        )
    except Exception:
        growth_series = None

    reanalyze_job = None
    if reanalyze:
        try:
            from basebuddy.plugins.plant_health.reanalyze import start_history_reanalyze

            target = None
            if growth_series and growth_series.get("series"):
                target = growth_series["series"].get("id")
            reanalyze_job = start_history_reanalyze(monitor_id, target_series_id=target)
        except Exception:
            reanalyze_job = {"status": "error", "error": "Could not start reanalyze"}

    return {
        "ok": True,
        "pattern_id": pattern_id,
        "mask_path": mask_path,
        "has_color_profile": "color_profile" in pattern,
        "coverage_hint": pattern.get("bbox"),
        "growth_series": growth_series,
        "reanalyze": reanalyze_job,
    }
