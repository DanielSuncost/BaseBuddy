"""Scheduled SAM re-segmentation growth tracking.

Proxies
-------
* **Mask area** — pixel count (and frame fraction) of the SAM mask from fixed
  anchor points. Tracks silhouette growth/shrink; sensitive to camera pose,
  lighting-induced SAM drift, and background confusion.
* **Green pixels in mask** — pixels inside the SAM mask that pass an HSV green
  foliage band (OpenCV H∈[25,95], S≥30, V≥30). Stronger foliage-volume proxy
  than raw area; still lighting-dependent and ignores non-green plant tissue.

Neither is absolute biomass; use relative change within one series/camera.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from basebuddy.plugins.plant_health.green_refine import (
    clamp_luma,
    clamp_threshold,
    refine_mask,
)

logger = logging.getLogger(__name__)

# OpenCV HSV green foliage band (matches plant_health metrics HSV fallback).
GREEN_H_MIN = 25
GREEN_H_MAX = 95
GREEN_S_MIN = 30
GREEN_V_MIN = 30

DEFAULT_GROWTH_INTERVAL_S = 3600  # 1 hour
DEFAULT_EXEMPLAR_INTERVAL_S = 24 * 3600  # daily stills
AREA_CHANGE_EXEMPLAR_RATIO = 0.15  # |Δarea|/prev ≥ 15% → save still

# Mask-evolution GIF caps (keep files small).
MASK_GIF_MAX_FRAMES = 48
MASK_GIF_RECENT_S = 72 * 3600  # keep all hourly samples in last 72h
MASK_GIF_MAX_SIDE = 480
MASK_GIF_DURATION_MS = 350


def growth_config(monitor: dict) -> Dict[str, Any]:
    """Normalized sam_growth settings from monitor config."""
    raw = monitor.get("sam_growth") or {}
    interval = int(
        raw.get("interval_s")
        or monitor.get("check_interval_s")
        or DEFAULT_GROWTH_INTERVAL_S
    )
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_s": max(60, interval),
        "exemplar_interval_s": int(
            raw.get("exemplar_interval_s") or DEFAULT_EXEMPLAR_INTERVAL_S
        ),
        "area_change_ratio": float(
            raw.get("area_change_ratio") or AREA_CHANGE_EXEMPLAR_RATIO
        ),
        "green_refine": clamp_threshold(raw.get("green_refine", 0)),
        "luma_carve": clamp_luma(raw.get("luma_carve", 0)),
    }


def _apply_cfg_refine(frame, mask, cfg: Dict[str, Any], template=None):
    return refine_mask(
        frame,
        mask,
        cfg.get("green_refine") or 0,
        cfg.get("luma_carve") or 0,
        template=template if template is not None else mask,
    )


def _locked_mask_for_frame(monitor: dict, series: dict, frame, crop) -> Optional[np.ndarray]:
    """Reuse the saved silhouette instead of re-running SAM every hour."""
    from basebuddy.plugins.plant_health.frame_crop import align_mask_to_frame
    from basebuddy.plugins.plant_health.metrics import load_saved_mask

    fh, fw = frame.shape[:2]
    mask = load_saved_mask((fh, fw), monitor)
    if mask is not None and int(np.sum(mask > 0)) > 0:
        return mask
    path = series.get("initial_mask_path")
    if not path:
        return None
    resolved = path if os.path.isfile(path) else None
    if not resolved:
        try:
            from basebuddy.plugins.plant_health.reanalyze import resolve_image_path
            resolved = resolve_image_path(path)
        except Exception:
            resolved = None
    if not resolved:
        return None
    raw = cv2.imread(resolved, cv2.IMREAD_GRAYSCALE)
    if raw is None or int(np.sum(raw > 0)) == 0:
        return None
    return align_mask_to_frame(raw, frame, crop)


def count_green_in_mask(
    frame_bgr: np.ndarray,
    mask: np.ndarray,
    *,
    h_min: int = GREEN_H_MIN,
    h_max: int = GREEN_H_MAX,
    s_min: int = GREEN_S_MIN,
    v_min: int = GREEN_V_MIN,
) -> Dict[str, Any]:
    """Threshold green foliage inside *mask*; return area + green counts.

    Green rule: OpenCV HSV with H in [h_min, h_max], S ≥ s_min, V ≥ v_min.
    """
    if frame_bgr is None or mask is None:
        return {
            "mask_area_px": 0,
            "mask_area_fraction": 0.0,
            "green_pixels_in_mask": 0,
            "total_mask_pixels": 0,
            "green_fraction_in_mask": 0.0,
            "frame_w": 0,
            "frame_h": 0,
            "quality_flags": ["no_input"],
        }

    h, w = frame_bgr.shape[:2]
    plant = mask > 0
    mask_px = int(np.sum(plant))
    total = h * w
    fraction = (mask_px / total) if total else 0.0

    flags: List[str] = []
    if mask_px == 0:
        flags.append("empty_mask")
        return {
            "mask_area_px": 0,
            "mask_area_fraction": 0.0,
            "green_pixels_in_mask": 0,
            "total_mask_pixels": 0,
            "green_fraction_in_mask": 0.0,
            "frame_w": w,
            "frame_h": h,
            "quality_flags": flags,
        }

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([h_min, s_min, v_min], dtype=np.uint8)
    upper = np.array([h_max, 255, 255], dtype=np.uint8)
    green = cv2.inRange(hsv, lower, upper)
    green_in = int(np.sum((green > 0) & plant))
    green_frac = green_in / max(1, mask_px)

    L_frame = float(np.mean(hsv[:, :, 2]) / 255.0)
    plant_s = float(np.mean(hsv[plant][:, 1])) if mask_px else 0.0
    plant_v = float(np.mean(hsv[plant][:, 2]) / 255.0) if mask_px else 0.0

    if mask_px < 80:
        flags.append("few_pixels")
    if green_frac < 0.05 and mask_px > 200:
        flags.append("low_green_in_mask")
    if L_frame < 0.08:
        flags.append("L_frame_low")
    if plant_v < 0.05:
        flags.append("L_plant_low")
    if plant_s < 12.0:
        flags.append("plant_desaturated")

    return {
        "mask_area_px": mask_px,
        "mask_area_fraction": round(fraction, 6),
        "green_pixels_in_mask": green_in,
        "total_mask_pixels": mask_px,
        "green_fraction_in_mask": round(green_frac, 5),
        "frame_w": w,
        "frame_h": h,
        "L_frame": round(L_frame, 4),
        "L_plant": round(plant_v, 4),
        "quality_flags": flags,
        "green_rule": {
            "space": "HSV_OpenCV",
            "h": [h_min, h_max],
            "s_min": s_min,
            "v_min": v_min,
            "note": "Foliage green band inside SAM mask; not ExG.",
        },
    }


def scale_points_to_frame(
    points: List,
    *,
    src_w: Optional[int],
    src_h: Optional[int],
    dst_w: int,
    dst_h: int,
) -> List[List[float]]:
    """Scale absolute click points from series frame size to current frame."""
    if not points:
        return []
    if not src_w or not src_h or src_w <= 0 or src_h <= 0:
        return [[float(p[0]), float(p[1])] for p in points]
    sx = dst_w / float(src_w)
    sy = dst_h / float(src_h)
    return [[float(p[0]) * sx, float(p[1]) * sy] for p in points]


def _series_still_dir(camera_id: int, series_id: int) -> str:
    from basebuddy.plugins.plant_health.mask_gif import growth_series_dir

    out_dir = growth_series_dir(camera_id, series_id)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _save_exemplar(camera_id: int, frame: np.ndarray, series_id: int) -> str:
    from basebuddy.core.upload_safety import safe_basename

    out_dir = _series_still_dir(camera_id, series_id)
    ts = int(time.time())
    fname = safe_basename(f"growth_{ts}.jpg") or f"growth_{ts}.jpg"
    path = os.path.join(out_dir, fname)
    cv2.imwrite(path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return path


def _save_mask_still(
    camera_id: int,
    series_id: int,
    frame: np.ndarray,
    mask: np.ndarray,
    *,
    sampled_at: Optional[float] = None,
) -> str:
    """Persist a tinted mask overlay still for GIF / review (compact JPEG)."""
    paths = _save_playback_stills(
        camera_id, series_id, frame, mask, sampled_at=sampled_at
    )
    return paths["mask_path"]


def _save_playback_stills(
    camera_id: int,
    series_id: int,
    frame: np.ndarray,
    mask: np.ndarray,
    *,
    sampled_at: Optional[float] = None,
) -> Dict[str, Optional[str]]:
    """Write overlay, mask-only silhouette, and compact raw stills."""
    from basebuddy.core.upload_safety import safe_basename
    from basebuddy.plugins.plant_health.mask_gif import render_mask_frame

    out_dir = _series_still_dir(camera_id, series_id)
    ts = int(sampled_at if sampled_at is not None else time.time())
    label = time.strftime("%b %d %H:%M", time.localtime(ts))
    overlay = render_mask_frame(
        mask, frame, mode="overlay", max_side=MASK_GIF_MAX_SIDE, label=label
    )
    silhouette = render_mask_frame(
        mask, None, mode="silhouette", max_side=MASK_GIF_MAX_SIDE, label=None
    )
    raw = frame
    h, w = raw.shape[:2]
    side = max(h, w)
    if side > MASK_GIF_MAX_SIDE:
        scale = MASK_GIF_MAX_SIDE / float(side)
        raw = cv2.resize(
            raw,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )

    overlay_name = safe_basename(f"mask_{ts}.jpg") or f"mask_{ts}.jpg"
    sil_name = safe_basename(f"sil_{ts}.jpg") or f"sil_{ts}.jpg"
    raw_name = safe_basename(f"raw_{ts}.jpg") or f"raw_{ts}.jpg"
    overlay_path = os.path.join(out_dir, overlay_name)
    sil_path = os.path.join(out_dir, sil_name)
    raw_path = os.path.join(out_dir, raw_name)
    cv2.imwrite(overlay_path, overlay, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    cv2.imwrite(sil_path, silhouette, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    cv2.imwrite(raw_path, raw, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return {
        "mask_path": overlay_path,
        "silhouette_path": sil_path,
        "raw_path": raw_path,
    }


def _refresh_mask_gif(camera_id: int, series_id: int) -> None:
    try:
        from basebuddy.plugins.plant_health.mask_gif import ensure_series_mask_gif

        ensure_series_mask_gif(series_id, camera_id, force=True)
    except Exception as exc:
        logger.warning("Mask GIF refresh failed for series %s: %s", series_id, exc)


def should_save_exemplar(
    series_id: int,
    metrics: Dict[str, Any],
    *,
    exemplar_interval_s: int = DEFAULT_EXEMPLAR_INTERVAL_S,
    area_change_ratio: float = AREA_CHANGE_EXEMPLAR_RATIO,
) -> bool:
    from basebuddy.plugins.plant_health.sam_series import (
        latest_exemplar_ts,
        previous_ok_sample,
    )

    last_ex = latest_exemplar_ts(series_id)
    if last_ex is None:
        return True
    if (time.time() - last_ex) >= exemplar_interval_s:
        return True
    prev = previous_ok_sample(series_id)
    if prev and prev.get("mask_area_px") and metrics.get("mask_area_px"):
        prev_a = float(prev["mask_area_px"])
        cur_a = float(metrics["mask_area_px"])
        if prev_a > 0 and abs(cur_a - prev_a) / prev_a >= area_change_ratio:
            return True
    return False


def run_growth_sample(
    monitor_id: str,
    *,
    series_id: Optional[int] = None,
    force: bool = False,
    trigger: str = "manual",
) -> Dict[str, Any]:
    """Stamp the saved mask onto a live frame, carve, and persist.

    SAM is not re-run on the schedule — that made outlines jump. New live
    samples use the locked silhouette plus carves, matching the top preview.
    Fails soft (returns ok=False) when the camera is unavailable.
    """
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.sam_series import (
        get_active_series,
        get_series,
        save_growth_sample,
    )
    from basebuddy.plugins.plant_health.segmentation import (
        capture_frame_bgr,
        run_sam_mask,
    )

    monitor = get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "error": "Monitor not found"}

    cfg = growth_config(monitor)
    if not force and not cfg["enabled"]:
        return {"ok": False, "error": "Growth tracking disabled", "skipped": True}

    series = get_series(series_id) if series_id else get_active_series(monitor_id)
    if not series:
        # One-shot bootstrap for monitors that had a mask before growth series existed.
        try:
            boot = ensure_series_from_existing_mask(monitor_id)
            if boot and boot.get("series"):
                series = boot["series"]
                # Prefer the bootstrap sample over an immediate second run.
                fs = boot.get("first_sample")
                if fs and fs.get("ok"):
                    return {
                        "ok": True,
                        "sample_id": fs.get("sample_id"),
                        "series_id": series["id"],
                        "metrics": fs.get("metrics"),
                        "exemplar_path": fs.get("exemplar_path"),
                        "trigger": fs.get("trigger") or "bootstrap_existing_mask",
                        "bootstrapped": True,
                        "has_anchor_points": boot.get("has_anchor_points"),
                        "static_mask": not boot.get("has_anchor_points"),
                    }
        except Exception as exc:
            logger.warning("Growth series bootstrap failed for %s: %s", monitor_id, exc)
    if not series:
        return {
            "ok": False,
            "error": "No active SAM growth series — use Edit mask to save a plant region",
            "skipped": True,
        }

    camera_id = int(series.get("camera_id", monitor.get("camera_id", 0)))
    crop = monitor.get("frame_crop")
    frame = capture_frame_bgr(camera_id, crop=crop)
    if frame is None:
        return {"ok": False, "error": "No frame from camera", "series_id": series["id"]}

    from basebuddy.plugins.plant_health.frame_crop import adapt_points_for_crop, align_mask_to_frame

    fh, fw = frame.shape[:2]
    points = adapt_points_for_crop(
        series.get("points") or [],
        src_w=series.get("frame_w"),
        src_h=series.get("frame_h"),
        dst_w=fw,
        dst_h=fh,
        crop=crop,
    )
    labels = [int(x) for x in (series.get("labels") or [])]
    if len(labels) != len(points):
        labels = labels[: len(points)]

    mask = _locked_mask_for_frame(monitor, series, frame, crop)
    err = None
    used_static_mask = mask is not None
    if mask is None and points and any(int(l) == 1 for l in labels):
        try:
            mask, err = run_sam_mask(frame, points, labels)
        except Exception as exc:
            logger.warning("SAM growth sample failed for %s: %s", monitor_id, exc)
            save_growth_sample(
                series["id"],
                monitor_id,
                {"quality_flags": ["sam_exception"], "frame_w": fw, "frame_h": fh},
                ok=False,
                error=str(exc),
            )
            return {"ok": False, "error": str(exc), "series_id": series["id"]}
    elif mask is None:
        err = "No saved mask — use Edit mask to save a plant region"

    if err or mask is None:
        save_growth_sample(
            series["id"],
            monitor_id,
            {"quality_flags": ["sam_unavailable"], "frame_w": fw, "frame_h": fh},
            ok=False,
            error=err or "SAM failed",
        )
        return {
            "ok": False,
            "error": err or "SAM failed",
            "series_id": series["id"],
            "sam_unavailable": True,
        }

    sam_area = int(np.sum(mask > 0))
    mask = _apply_cfg_refine(frame, mask, cfg)

    metrics = count_green_in_mask(frame, mask)
    if cfg.get("green_refine") or cfg.get("luma_carve"):
        metrics["green_refine"] = cfg.get("green_refine") or 0
        metrics["luma_carve"] = cfg.get("luma_carve") or 0
        metrics["sam_area_px"] = sam_area
    metrics["trigger"] = trigger
    if used_static_mask:
        metrics.setdefault("quality_flags", []).append("locked_mask")

    exemplar_path = None
    if should_save_exemplar(
        series["id"],
        metrics,
        exemplar_interval_s=cfg["exemplar_interval_s"],
        area_change_ratio=cfg["area_change_ratio"],
    ):
        try:
            exemplar_path = _save_exemplar(camera_id, frame, series["id"])
        except Exception as exc:
            logger.warning("Exemplar save failed: %s", exc)
            metrics.setdefault("quality_flags", []).append("exemplar_failed")

    stills = {"mask_path": None, "silhouette_path": None, "raw_path": None}
    try:
        stills = _save_playback_stills(camera_id, series["id"], frame, mask)
    except Exception as exc:
        logger.warning("Mask still save failed: %s", exc)
        metrics.setdefault("quality_flags", []).append("mask_still_failed")

    sample_id = save_growth_sample(
        series["id"],
        monitor_id,
        metrics,
        exemplar_path=exemplar_path,
        mask_path=stills.get("mask_path"),
        silhouette_path=stills.get("silhouette_path"),
        raw_path=stills.get("raw_path"),
        ok=True,
    )
    if stills.get("mask_path"):
        _refresh_mask_gif(camera_id, series["id"])
    return {
        "ok": True,
        "sample_id": sample_id,
        "series_id": series["id"],
        "metrics": metrics,
        "exemplar_path": exemplar_path,
        "mask_path": stills.get("mask_path"),
        "silhouette_path": stills.get("silhouette_path"),
        "raw_path": stills.get("raw_path"),
        "trigger": trigger,
        "static_mask": used_static_mask,
    }


def maybe_run_growth_sample(monitor_id: str, trigger: str = "on_schedule") -> Optional[Dict[str, Any]]:
    """Run a growth sample if interval due; never raises."""
    try:
        from basebuddy.plugins.plant_health.config import get_monitor
        from basebuddy.plugins.plant_health.sam_series import (
            get_active_series,
            latest_growth_sample_ts,
        )

        monitor = get_monitor(monitor_id)
        if not monitor:
            return None
        cfg = growth_config(monitor)
        if not cfg["enabled"]:
            return None
        series = get_active_series(monitor_id)
        if not series:
            boot = ensure_series_from_existing_mask(monitor_id)
            series = (boot or {}).get("series")
        if not series:
            return None
        last = latest_growth_sample_ts(monitor_id, series_id=series["id"])
        manual = trigger == "manual"
        if not manual and last is not None and (time.time() - last) < cfg["interval_s"]:
            return {"ok": True, "skipped": True, "reason": "not_due"}
        return run_growth_sample(monitor_id, force=manual, trigger=trigger)
    except Exception as exc:
        logger.warning("Growth sample soft-fail for %s: %s", monitor_id, exc)
        return {"ok": False, "error": str(exc)}


def record_sample_from_mask(
    monitor_id: str,
    series_id: int,
    frame: np.ndarray,
    mask: np.ndarray,
    *,
    camera_id: int,
    trigger: str = "series_created",
    force_exemplar: bool = True,
) -> Dict[str, Any]:
    """Persist growth metrics from an already-computed mask (no SAM re-run)."""
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.sam_series import save_growth_sample

    cfg = growth_config(get_monitor(monitor_id) or {})
    mask = _apply_cfg_refine(frame, mask, cfg)
    metrics = count_green_in_mask(frame, mask)
    if cfg.get("green_refine") or cfg.get("luma_carve"):
        metrics["green_refine"] = cfg.get("green_refine") or 0
        metrics["luma_carve"] = cfg.get("luma_carve") or 0
    metrics["trigger"] = trigger
    exemplar_path = None
    if force_exemplar or should_save_exemplar(series_id, metrics):
        try:
            exemplar_path = _save_exemplar(camera_id, frame, series_id)
        except Exception as exc:
            logger.warning("Exemplar save failed: %s", exc)
    stills = {"mask_path": None, "silhouette_path": None, "raw_path": None}
    try:
        stills = _save_playback_stills(camera_id, series_id, frame, mask)
    except Exception as exc:
        logger.warning("Mask still save failed: %s", exc)
    sample_id = save_growth_sample(
        series_id,
        monitor_id,
        metrics,
        exemplar_path=exemplar_path,
        mask_path=stills.get("mask_path"),
        silhouette_path=stills.get("silhouette_path"),
        raw_path=stills.get("raw_path"),
        ok=True,
    )
    if stills.get("mask_path"):
        _refresh_mask_gif(camera_id, series_id)
    return {
        "ok": True,
        "sample_id": sample_id,
        "series_id": series_id,
        "metrics": metrics,
        "exemplar_path": exemplar_path,
        "mask_path": stills.get("mask_path"),
        "silhouette_path": stills.get("silhouette_path"),
        "raw_path": stills.get("raw_path"),
        "trigger": trigger,
    }


def create_series_from_save(
    monitor_id: str,
    camera_id: int,
    points: List,
    labels: List,
    *,
    frame_w: Optional[int] = None,
    frame_h: Optional[int] = None,
    initial_mask_path: Optional[str] = None,
    take_first_sample: bool = True,
    frame: Optional[np.ndarray] = None,
    mask: Optional[np.ndarray] = None,
    force_new: bool = False,
) -> Dict[str, Any]:
    """Save editor points onto the active series, or start a new one.

    Re-saving a mask used to archive the current series and start a blank
    chart. Default now updates anchors in place so history continues. Pass
    ``force_new=True`` for an explicit new series (new point-set / reset).
    """
    from basebuddy.plugins.plant_health.sam_series import (
        create_series,
        get_active_series,
        update_series_anchors,
    )

    updated_existing = False
    active = get_active_series(monitor_id) if not force_new else None
    if active:
        series = update_series_anchors(
            active["id"],
            points,
            labels,
            frame_w=frame_w,
            frame_h=frame_h,
            initial_mask_path=initial_mask_path,
        ) or active
        updated_existing = True
        trigger = "mask_updated"
    else:
        series = create_series(
            monitor_id,
            camera_id,
            points,
            labels,
            frame_w=frame_w,
            frame_h=frame_h,
            initial_mask_path=initial_mask_path,
            activate=True,
        )
        trigger = "series_created"
    sample = None
    if take_first_sample:
        if frame is not None and mask is not None:
            sample = record_sample_from_mask(
                monitor_id,
                series["id"],
                frame,
                mask,
                camera_id=camera_id,
                trigger=trigger,
                force_exemplar=True,
            )
        else:
            sample = run_growth_sample(
                monitor_id,
                series_id=series["id"],
                force=True,
                trigger=trigger,
            )
    return {
        "ok": True,
        "series": series,
        "first_sample": sample,
        "updated_existing": updated_existing,
        "new_series": not updated_existing,
    }


def _absolute_points_from_relative(
    fg_rel: List,
    bg_rel: List,
    *,
    frame_w: int,
    frame_h: int,
) -> tuple:
    """Convert pattern-relative points to absolute pixel coords + labels."""
    points: List[List[float]] = []
    labels: List[int] = []
    for p in fg_rel or []:
        if not p or len(p) < 2:
            continue
        points.append([float(p[0]) * frame_w, float(p[1]) * frame_h])
        labels.append(1)
    for p in bg_rel or []:
        if not p or len(p) < 2:
            continue
        points.append([float(p[0]) * frame_w, float(p[1]) * frame_h])
        labels.append(0)
    return points, labels


def _extract_saved_anchors(monitor: dict) -> Dict[str, Any]:
    """Pull anchor points + mask path from monitor / pattern for bootstrap.

    Preference: segmentation.sam_points (new saves) → pattern relative points
    → mask bitmap alone (no anchors).
    """
    from basebuddy.plugins.plant_health.metrics import load_pattern_entry, resolve_mask_path

    seg = monitor.get("segmentation") or {}
    camera_id = int(seg.get("pattern_camera_id", monitor.get("camera_id", 0)))
    mask_path = resolve_mask_path(monitor)
    points: List = list(seg.get("sam_points") or [])
    labels: List = [int(x) for x in (seg.get("sam_labels") or [])]
    frame_w = seg.get("frame_w")
    frame_h = seg.get("frame_h")

    if points and any(int(l) == 1 for l in labels):
        if len(labels) != len(points):
            labels = labels[: len(points)]
            while len(labels) < len(points):
                labels.append(1)
        return {
            "camera_id": camera_id,
            "points": points,
            "labels": labels,
            "frame_w": int(frame_w) if frame_w else None,
            "frame_h": int(frame_h) if frame_h else None,
            "mask_path": mask_path,
            "has_anchor_points": True,
            "source": "monitor_segmentation",
        }

    pid = int(seg.get("pattern_id", -1))
    pat = load_pattern_entry(camera_id, pid)
    pattern = (pat or {}).get("pattern") or {}
    shape = pattern.get("mask_shape") or []
    if (not frame_w or not frame_h) and len(shape) >= 2:
        frame_h, frame_w = int(shape[0]), int(shape[1])
    if (not frame_w or not frame_h) and mask_path and os.path.isfile(mask_path):
        m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        if m is not None:
            frame_h, frame_w = int(m.shape[0]), int(m.shape[1])

    fg = pattern.get("fg_points_relative") or []
    bg = pattern.get("bg_points_relative") or []
    if frame_w and frame_h and (fg or bg):
        points, labels = _absolute_points_from_relative(
            fg, bg, frame_w=int(frame_w), frame_h=int(frame_h)
        )
        if points and any(l == 1 for l in labels):
            return {
                "camera_id": camera_id,
                "points": points,
                "labels": labels,
                "frame_w": int(frame_w),
                "frame_h": int(frame_h),
                "mask_path": mask_path or pattern.get("mask_path") or (pat or {}).get("mask_path"),
                "has_anchor_points": True,
                "source": "pattern_relative",
                "reference_image": (pat or {}).get("reference_image"),
            }

    if mask_path:
        if not frame_w or not frame_h:
            m = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if m is not None:
                frame_h, frame_w = int(m.shape[0]), int(m.shape[1])
        return {
            "camera_id": camera_id,
            "points": [],
            "labels": [],
            "frame_w": int(frame_w) if frame_w else None,
            "frame_h": int(frame_h) if frame_h else None,
            "mask_path": mask_path,
            "has_anchor_points": False,
            "source": "mask_bitmap",
            "reference_image": (pat or {}).get("reference_image") if pat else None,
        }

    return {
        "camera_id": camera_id,
        "points": [],
        "labels": [],
        "frame_w": None,
        "frame_h": None,
        "mask_path": None,
        "has_anchor_points": False,
        "source": None,
    }


def _load_bootstrap_frame_and_mask(
    anchors: Dict[str, Any],
) -> tuple:
    """Best-effort frame + mask for an initial growth sample without re-SAM."""
    from basebuddy.plugins.plant_health.segmentation import capture_frame_bgr

    mask_path = anchors.get("mask_path")
    mask = None
    if mask_path and os.path.isfile(mask_path):
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

    from basebuddy.plugins.plant_health.frame_crop import align_mask_to_frame

    crop = anchors.get("frame_crop")
    frame = capture_frame_bgr(int(anchors.get("camera_id") or 0), crop=crop)
    if frame is None:
        ref = anchors.get("reference_image")
        if ref and os.path.isfile(ref):
            frame = cv2.imread(ref)
            if frame is not None and crop:
                from basebuddy.plugins.plant_health.frame_crop import apply_frame_crop

                frame = apply_frame_crop(frame, crop)

    if frame is not None and mask is not None:
        mask = align_mask_to_frame(mask, frame, crop)
    elif mask is not None and frame is None:
        # Synthesize a neutral frame so area metrics still record.
        h, w = mask.shape[:2]
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        mask = (mask > 0).astype(np.uint8) * 255

    return frame, mask


def ensure_series_from_existing_mask(monitor_id: str) -> Optional[Dict[str, Any]]:
    """Create an active growth series from a previously saved SAM mask/points.

    Used when the monitor has a region (mask and/or pattern anchors) but no
    ``plant_sam_series`` row yet (masks saved before growth tracking shipped).

    * With anchor points: series supports scheduled SAM re-segmentation.
    * Mask bitmap only: series gets an initial area/green sample from the
      saved mask; scheduled samples reuse the static bitmap until the user
      re-saves via **Edit mask** (which stores anchors going forward).
    """
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.sam_series import (
        get_active_series,
        list_series,
    )

    monitor = get_monitor(monitor_id)
    if not monitor:
        return None
    if get_active_series(monitor_id) or list_series(monitor_id, limit=1):
        return None

    anchors = _extract_saved_anchors(monitor)
    anchors["frame_crop"] = monitor.get("frame_crop")
    if not anchors.get("mask_path") and not anchors.get("has_anchor_points"):
        return None

    label = (
        "Bootstrapped from saved mask"
        if not anchors.get("has_anchor_points")
        else "Bootstrapped from saved region"
    )
    from basebuddy.plugins.plant_health.sam_series import create_series

    series = create_series(
        monitor_id,
        int(anchors["camera_id"]),
        anchors.get("points") or [],
        anchors.get("labels") or [],
        frame_w=anchors.get("frame_w"),
        frame_h=anchors.get("frame_h"),
        initial_mask_path=anchors.get("mask_path"),
        label=label,
        activate=True,
    )

    sample = None
    frame, mask = _load_bootstrap_frame_and_mask(anchors)
    if frame is not None and mask is not None:
        from basebuddy.plugins.plant_health.sam_series import save_growth_sample

        cfg = growth_config(get_monitor(monitor_id) or {})
        mask = _apply_cfg_refine(frame, mask, cfg)
        metrics = count_green_in_mask(frame, mask)
        metrics["trigger"] = "bootstrap_existing_mask"
        if cfg.get("green_refine") or cfg.get("luma_carve"):
            metrics["green_refine"] = cfg.get("green_refine") or 0
            metrics["luma_carve"] = cfg.get("luma_carve") or 0
        if not anchors.get("has_anchor_points"):
            metrics.setdefault("quality_flags", []).append("static_mask_no_anchors")
        exemplar_path = None
        try:
            exemplar_path = _save_exemplar(
                int(anchors["camera_id"]), frame, series["id"]
            )
        except Exception as exc:
            logger.warning("Bootstrap exemplar save failed: %s", exc)
        stills = {"mask_path": None, "silhouette_path": None, "raw_path": None}
        try:
            stills = _save_playback_stills(
                int(anchors["camera_id"]), series["id"], frame, mask
            )
        except Exception as exc:
            logger.warning("Bootstrap mask still save failed: %s", exc)
        sample_id = save_growth_sample(
            series["id"],
            monitor_id,
            metrics,
            exemplar_path=exemplar_path,
            mask_path=stills.get("mask_path"),
            silhouette_path=stills.get("silhouette_path"),
            raw_path=stills.get("raw_path"),
            ok=True,
        )
        if stills.get("mask_path"):
            _refresh_mask_gif(int(anchors["camera_id"]), series["id"])
        sample = {
            "ok": True,
            "sample_id": sample_id,
            "series_id": series["id"],
            "metrics": metrics,
            "exemplar_path": exemplar_path,
            "mask_path": stills.get("mask_path"),
            "silhouette_path": stills.get("silhouette_path"),
            "raw_path": stills.get("raw_path"),
            "trigger": "bootstrap_existing_mask",
        }

    return {
        "ok": True,
        "series": series,
        "first_sample": sample,
        "bootstrapped": True,
        "has_anchor_points": bool(anchors.get("has_anchor_points")),
        "source": anchors.get("source"),
    }


def apply_carves_to_series(monitor_id: str, series_id: int) -> Dict[str, Any]:
    """Re-apply current green + luma carves to every stored still in *series_id*.

    Uses the saved mask bitmap (deterministic) on each raw/exemplar frame.
    Does not create a new series; metrics and playback stills are rewritten.
    """
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.frame_crop import (
        align_mask_to_frame,
        prepare_historical_frame,
    )
    from basebuddy.plugins.plant_health.metrics import analyze_color_profile, resolve_mask_path
    from basebuddy.plugins.plant_health.reanalyze import resolve_image_path
    from basebuddy.plugins.plant_health.sam_series import (
        get_series,
        list_growth_samples,
        update_growth_sample,
    )

    monitor = get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "error": "Monitor not found"}
    series = get_series(int(series_id))
    if not series or series.get("monitor_id") != monitor_id:
        return {"ok": False, "error": "Series not found"}

    mask_path = resolve_mask_path(monitor) or series.get("initial_mask_path")
    mask_bitmap = None
    resolved = resolve_image_path(mask_path) if mask_path else None
    if resolved:
        mask_bitmap = cv2.imread(resolved, cv2.IMREAD_GRAYSCALE)
    if mask_bitmap is None or int(np.sum(mask_bitmap > 0)) == 0:
        return {"ok": False, "error": "No saved mask to reproject"}

    cfg = growth_config(monitor)
    crop = monitor.get("frame_crop")
    camera_id = int(series.get("camera_id", monitor.get("camera_id", 0)))
    samples = list_growth_samples(int(series_id), limit=5000)
    ok_count = 0
    skip_count = 0
    for sample in samples:
        if not sample.get("ok"):
            skip_count += 1
            continue
        src = resolve_image_path(sample.get("raw_path")) or resolve_image_path(
            sample.get("exemplar_path")
        )
        if not src:
            skip_count += 1
            continue
        frame = cv2.imread(src, cv2.IMREAD_COLOR)
        if frame is None:
            skip_count += 1
            continue
        frame = prepare_historical_frame(frame, crop)
        mask = align_mask_to_frame(mask_bitmap, frame, None)
        if mask is None:
            skip_count += 1
            continue
        mask = _apply_cfg_refine(frame, mask, cfg)
        metrics = count_green_in_mask(frame, mask)
        metrics["trigger"] = "apply_carves"
        metrics["green_refine"] = cfg.get("green_refine") or 0
        metrics["luma_carve"] = cfg.get("luma_carve") or 0
        stills = {"mask_path": sample.get("mask_path"), "silhouette_path": sample.get("silhouette_path"), "raw_path": sample.get("raw_path")}
        try:
            stills = _save_playback_stills(
                camera_id,
                series["id"],
                frame,
                mask,
                sampled_at=sample.get("sampled_at"),
            )
        except Exception as exc:
            logger.warning("Apply-carves still failed: %s", exc)
        update_growth_sample(
            int(sample["id"]),
            metrics,
            mask_path=stills.get("mask_path"),
            silhouette_path=stills.get("silhouette_path"),
            raw_path=stills.get("raw_path") or sample.get("raw_path"),
            ok=True,
        )
        color = analyze_color_profile(frame, mask)
        if color:
            try:
                from basebuddy.plugins.plant_health.db import update_color_sample_for_series

                update_color_sample_for_series(
                    monitor_id,
                    int(series_id),
                    float(sample["sampled_at"]),
                    color,
                )
            except Exception:
                pass
        ok_count += 1
    if ok_count:
        _refresh_mask_gif(camera_id, series["id"])
    return {
        "ok": ok_count > 0,
        "series_id": series["id"],
        "ok_count": ok_count,
        "skip_count": skip_count,
        "green_refine": cfg.get("green_refine") or 0,
        "luma_carve": cfg.get("luma_carve") or 0,
        "error": None if ok_count else "No frames could be recarved",
    }
