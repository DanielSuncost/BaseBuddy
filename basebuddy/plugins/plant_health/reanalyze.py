"""Replay saved stills with the current crop + mask into a new series.

Does not overwrite existing growth series or the original color timeline.
Each historical JPEG is cropped with the same normalized ROI, then the saved
mask bitmap is resized onto that crop (deterministic; no SAM re-run).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from basebuddy.plugins.plant_health.green_refine import refine_mask
from basebuddy.plugins.plant_health.sam_growth import (
    _refresh_mask_gif,
    _save_exemplar,
    _save_playback_stills,
    count_green_in_mask,
    growth_config,
)

logger = logging.getLogger(__name__)

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_DEDUP_S = 45.0
_ARCHIVE_INTERVAL_S = 3600.0
_STILL_NAME_RE = re.compile(r"^(\d{8})_(\d{6})")
_PLANT_NAME_RE = re.compile(r"plant_(\d+)")


def get_reanalyze_job(monitor_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(monitor_id)
        return dict(job) if job else None


def _set_job(monitor_id: str, **fields: Any) -> Dict[str, Any]:
    with _LOCK:
        cur = _JOBS.setdefault(monitor_id, {
            "status": "queued",
            "done": 0,
            "total": 0,
            "ok_count": 0,
            "skip_count": 0,
            "series_id": None,
            "error": None,
            "started_at": time.time(),
        })
        cur.update(fields)
        return dict(cur)


def resolve_image_path(path: Optional[str]) -> Optional[str]:
    """Resolve a stored still path (absolute, /media/…, or /stills/…)."""
    if not path:
        return None
    text = str(path).replace("\\", "/")
    if os.path.isfile(path):
        return path
    if os.path.isfile(text):
        return text
    candidates: List[str] = []
    try:
        from basebuddy.core.paths import abs_data_path, get_repo_root
        from basebuddy.modules.config import MEDIA_BASE_DIR

        root = get_repo_root()
        media = abs_data_path(MEDIA_BASE_DIR or "media")
        if text.startswith("/media/"):
            rel = text[len("/media/"):]
            candidates.append(os.path.join(media, rel))
            candidates.append(os.path.join(root, "media", rel))
        elif text.startswith("media/"):
            candidates.append(os.path.join(media, text[len("media/"):]))
            candidates.append(os.path.join(root, text))
        if text.startswith("/stills/"):
            candidates.append(os.path.join(root, text.lstrip("/")))
        elif text.startswith("stills/"):
            candidates.append(os.path.join(root, text))
        if not os.path.isabs(text):
            candidates.append(os.path.join(root, text))
    except Exception:
        pass
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return None


def timestamp_from_filename(name: str) -> Optional[float]:
    """Parse `YYYYMMDD_HHMMSS.jpg` or `plant_<unix>.jpg` still names."""
    base = os.path.basename(name or "")
    m = _STILL_NAME_RE.match(base)
    if m:
        try:
            dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
            return dt.timestamp()
        except ValueError:
            return None
    m = _PLANT_NAME_RE.search(base)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _archive_dirs(camera_id: int) -> List[str]:
    dirs: List[str] = []
    try:
        from basebuddy.core.paths import abs_data_path, get_repo_root
        from basebuddy.modules.config import MEDIA_BASE_DIR

        root = get_repo_root()
        media = abs_data_path(MEDIA_BASE_DIR or "media")
        dirs.extend([
            os.path.join(root, "stills", f"camera_{int(camera_id)}"),
            os.path.join(root, "media", "plant_health", f"cam{int(camera_id) + 1}"),
            os.path.join(media, "plant_health", f"cam{int(camera_id) + 1}"),
        ])
    except Exception:
        pass
    seen = set()
    out: List[str] = []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def iter_archive_stills(camera_id: int) -> List[Dict[str, Any]]:
    """Walk camera + plant-health still folders that still exist on disk."""
    found: List[Dict[str, Any]] = []
    seen_paths = set()
    for folder in _archive_dirs(camera_id):
        kind = "camera_still" if os.path.basename(folder).startswith("camera_") else "plant_archive"
        for dp, _dns, fns in os.walk(folder):
            if os.path.basename(dp) == "sam_growth":
                continue
            for fn in fns:
                if not fn.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                path = os.path.join(dp, fn)
                if path in seen_paths:
                    continue
                ts = timestamp_from_filename(fn)
                if ts is None:
                    continue
                seen_paths.add(path)
                found.append({"sampled_at": ts, "path": path, "kind": kind})
    return found


def collect_source_frames(
    monitor_id: str,
    *,
    exclude_series_id: Optional[int] = None,
    camera_id: Optional[int] = None,
    interval_s: float = _ARCHIVE_INTERVAL_S,
) -> List[Dict[str, Any]]:
    """Unique historical stills, preferring color JPEGs, then series, then disk archives.

    Camera still folders can hold a frame a minute. Archive extras are thinned
    to *interval_s* so a rebuild can span every day that still has files.
    """
    from basebuddy.plugins.plant_health.db import list_color_source_stills
    from basebuddy.plugins.plant_health.sam_series import list_growth_samples, list_series

    if camera_id is None:
        try:
            from basebuddy.plugins.plant_health.config import get_monitor
            mon = get_monitor(monitor_id)
            if mon and mon.get("camera_id") is not None:
                camera_id = int(mon["camera_id"])
        except Exception:
            camera_id = None
    if camera_id is None:
        series_rows = list_series(monitor_id, limit=1)
        if series_rows:
            camera_id = int(series_rows[0].get("camera_id") or 0)

    ranked: Dict[int, Dict[str, Any]] = {}

    def consider(ts: float, path: Optional[str], rank: int, kind: str) -> None:
        if not path:
            return
        resolved = path if os.path.isfile(path) else resolve_image_path(path)
        if not resolved:
            return
        try:
            ts_f = float(ts)
        except (TypeError, ValueError):
            return
        key = int(round(ts_f / _DEDUP_S))
        prev = ranked.get(key)
        if prev and prev["rank"] <= rank:
            return
        ranked[key] = {
            "sampled_at": ts_f,
            "path": resolved,
            "kind": kind,
            "rank": rank,
        }

    for row in list_color_source_stills(monitor_id):
        consider(row.get("sampled_at"), row.get("image_path"), 0, "color")

    for series in list_series(monitor_id, limit=80):
        sid = series.get("id")
        if exclude_series_id is not None and sid == exclude_series_id:
            continue
        for sample in list_growth_samples(int(sid), limit=5000):
            ts = sample.get("sampled_at")
            consider(ts, sample.get("exemplar_path"), 1, "exemplar")
            consider(ts, sample.get("raw_path"), 2, "raw")

    occupied_hours = {
        int(v["sampled_at"] // max(60.0, float(interval_s)))
        for v in ranked.values()
    }
    if camera_id is not None:
        for row in iter_archive_stills(int(camera_id)):
            hour = int(float(row["sampled_at"]) // max(60.0, float(interval_s)))
            if hour in occupied_hours:
                continue
            consider(row["sampled_at"], row["path"], 3, row.get("kind") or "archive")
            if int(round(float(row["sampled_at"]) / _DEDUP_S)) in ranked:
                occupied_hours.add(hour)

    out = sorted(ranked.values(), key=lambda r: r["sampled_at"])
    return out


def _load_mask_bitmap(monitor: dict, series: dict) -> Optional[np.ndarray]:
    for candidate in (
        (monitor.get("segmentation") or {}).get("mask_path"),
        series.get("initial_mask_path"),
    ):
        resolved = resolve_image_path(candidate) if candidate else None
        if not resolved:
            continue
        mask = cv2.imread(resolved, cv2.IMREAD_GRAYSCALE)
        if mask is not None and int(np.sum(mask > 0)) > 0:
            return mask
    return None


def _target_series(monitor_id: str, monitor: dict, target_series_id: Optional[int]) -> dict:
    from basebuddy.plugins.plant_health.sam_series import (
        create_series,
        get_active_series,
        get_series,
        set_series_label,
    )

    if target_series_id is not None:
        series = get_series(int(target_series_id))
        if not series or series.get("monitor_id") != monitor_id:
            raise ValueError("Target series not found")
        set_series_label(
            series["id"],
            time.strftime("Rebuilt %Y-%m-%d %H:%M", time.localtime()),
        )
        return get_series(series["id"]) or series

    active = get_active_series(monitor_id)
    seg = monitor.get("segmentation") or {}
    points = list((active or {}).get("points") or seg.get("sam_points") or [])
    labels = [int(x) for x in ((active or {}).get("labels") or seg.get("sam_labels") or [])]
    return create_series(
        monitor_id,
        int((active or {}).get("camera_id", monitor.get("camera_id", 0))),
        points,
        labels,
        frame_w=(active or {}).get("frame_w") or seg.get("frame_w"),
        frame_h=(active or {}).get("frame_h") or seg.get("frame_h"),
        initial_mask_path=(active or {}).get("initial_mask_path") or seg.get("mask_path"),
        label=time.strftime("Rebuilt %Y-%m-%d %H:%M", time.localtime()),
        activate=True,
    )


def reanalyze_history(
    monitor_id: str,
    *,
    target_series_id: Optional[int] = None,
    progress_cb=None,
) -> Dict[str, Any]:
    """Synchronously rebuild a new series + color dataset from stored stills."""
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.db import save_color_sample
    from basebuddy.plugins.plant_health.frame_crop import (
        align_mask_to_frame,
        prepare_historical_frame,
    )
    from basebuddy.plugins.plant_health.metrics import analyze_color_profile
    from basebuddy.plugins.plant_health.sam_series import save_growth_sample

    monitor = get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "error": "Monitor not found"}

    series = _target_series(monitor_id, monitor, target_series_id)
    camera_id = int(series.get("camera_id", monitor.get("camera_id", 0)))
    crop = monitor.get("frame_crop")
    cfg = growth_config(monitor)
    mask_bitmap = _load_mask_bitmap(monitor, series)
    if mask_bitmap is None:
        return {"ok": False, "error": "No saved mask to reproject — save a region first"}

    sources = collect_source_frames(
        monitor_id,
        exclude_series_id=series["id"],
        camera_id=camera_id,
        interval_s=float(cfg.get("interval_s") or _ARCHIVE_INTERVAL_S),
    )
    if not sources:
        return {
            "ok": False,
            "error": "No historical stills found to reproject",
            "series_id": series["id"],
        }
    last_exemplar_ts = 0.0
    ok_count = 0
    skip_count = 0
    if progress_cb:
        progress_cb(0, len(sources), series["id"])

    for i, src in enumerate(sources):
        frame = cv2.imread(src["path"], cv2.IMREAD_COLOR)
        if frame is None:
            skip_count += 1
            if progress_cb:
                progress_cb(i + 1, len(sources), series["id"])
            continue
        frame = prepare_historical_frame(frame, crop)
        if frame is None:
            skip_count += 1
            if progress_cb:
                progress_cb(i + 1, len(sources), series["id"])
            continue
        mask = align_mask_to_frame(mask_bitmap, frame, None)
        if mask is None or int(np.sum(mask > 0)) == 0:
            skip_count += 1
            if progress_cb:
                progress_cb(i + 1, len(sources), series["id"])
            continue
        mask = refine_mask(
            frame, mask, cfg.get("green_refine") or 0, cfg.get("luma_carve") or 0
        )
        metrics = count_green_in_mask(frame, mask)
        metrics["trigger"] = "history_reanalyze"
        metrics["source_kind"] = src.get("kind")
        if cfg.get("green_refine") or cfg.get("luma_carve"):
            metrics["green_refine"] = cfg.get("green_refine") or 0
            metrics["luma_carve"] = cfg.get("luma_carve") or 0
            metrics["sam_area_px"] = int(np.sum(mask > 0))
        color = analyze_color_profile(frame, mask)
        ts = float(src["sampled_at"])
        exemplar_path = None
        if last_exemplar_ts <= 0 or (ts - last_exemplar_ts) >= 86400:
            try:
                exemplar_path = _save_exemplar(camera_id, frame, series["id"])
                last_exemplar_ts = ts
            except Exception as exc:
                logger.warning("Reanalyze exemplar failed: %s", exc)
        stills = {"mask_path": None, "silhouette_path": None, "raw_path": None}
        try:
            stills = _save_playback_stills(
                camera_id, series["id"], frame, mask, sampled_at=ts
            )
        except Exception as exc:
            logger.warning("Reanalyze still failed: %s", exc)
        save_growth_sample(
            series["id"],
            monitor_id,
            metrics,
            exemplar_path=exemplar_path,
            mask_path=stills.get("mask_path"),
            silhouette_path=stills.get("silhouette_path"),
            raw_path=stills.get("raw_path"),
            ok=True,
            sampled_at=ts,
        )
        if color:
            try:
                save_color_sample(
                    monitor_id,
                    camera_id,
                    color,
                    image_path=src["path"],
                    sampled_at=ts,
                    series_id=series["id"],
                )
            except Exception as exc:
                logger.warning("Reanalyze color sample failed: %s", exc)
        ok_count += 1
        if progress_cb:
            progress_cb(i + 1, len(sources), series["id"])

    if ok_count:
        _refresh_mask_gif(camera_id, series["id"])

    return {
        "ok": ok_count > 0,
        "series_id": series["id"],
        "ok_count": ok_count,
        "skip_count": skip_count,
        "total": len(sources),
        "error": None if ok_count else "Could not reproject any stills",
    }


def start_history_reanalyze(
    monitor_id: str,
    *,
    target_series_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Start a background reanalyze. One job per monitor."""
    with _LOCK:
        existing = _JOBS.get(monitor_id)
        if existing and existing.get("status") in ("queued", "running"):
            return dict(existing)

    job = _set_job(
        monitor_id,
        status="queued",
        done=0,
        total=0,
        ok_count=0,
        skip_count=0,
        series_id=target_series_id,
        error=None,
        started_at=time.time(),
    )

    def _run() -> None:
        def progress(done: int, total: int, series_id: int) -> None:
            _set_job(
                monitor_id,
                status="running",
                done=done,
                total=total,
                series_id=series_id,
            )

        _set_job(monitor_id, status="running")
        try:
            result = reanalyze_history(
                monitor_id,
                target_series_id=target_series_id,
                progress_cb=progress,
            )
            _set_job(
                monitor_id,
                status="done" if result.get("ok") else "error",
                done=result.get("total") or 0,
                total=result.get("total") or 0,
                ok_count=result.get("ok_count") or 0,
                skip_count=result.get("skip_count") or 0,
                series_id=result.get("series_id"),
                error=result.get("error"),
                finished_at=time.time(),
            )
        except Exception as exc:
            logger.exception("History reanalyze failed for %s", monitor_id)
            _set_job(
                monitor_id,
                status="error",
                error=str(exc),
                finished_at=time.time(),
            )

    threading.Thread(target=_run, name=f"plant-reanalyze-{monitor_id}", daemon=True).start()
    return job
