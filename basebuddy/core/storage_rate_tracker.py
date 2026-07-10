"""Measure media write rates from disk samples, file mtimes, and the events DB."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("basebuddy")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov"}
_ALL_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS

_HISTORY_MAX_SAMPLES = 96
_SAMPLE_MIN_INTERVAL_S = 1800  # 30 minutes
_INGEST_CACHE_TTL_S = 3600  # 1 hour


def _history_path(project_root: str) -> str:
    return os.path.join(project_root, ".storage_rate_history.json")


def _ingest_cache_path(project_root: str) -> str:
    return os.path.join(project_root, ".storage_ingest_cache.json")


def _load_json(path: str) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.debug("Could not load %s: %s", path, exc)
    return {}


def _save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception as exc:
        logger.warning("Could not save %s: %s", path, exc)


def record_size_sample(project_root: str, category_sizes: Dict[str, int]) -> None:
    """Append a timestamped size snapshot (throttled)."""
    path = _history_path(project_root)
    state = _load_json(path)
    samples: List[dict] = state.get("samples") or []
    now = time.time()

    if samples and (now - float(samples[-1].get("ts") or 0)) < _SAMPLE_MIN_INTERVAL_S:
        return

    samples.append({"ts": now, "categories": {k: int(v) for k, v in category_sizes.items()}})
    samples = samples[-_HISTORY_MAX_SAMPLES:]
    state["samples"] = samples
    _save_json(path, state)


def _rates_from_history(samples: List[dict], category: str) -> Optional[float]:
    """Net GB/day from oldest sample in the last 24h (can be negative if retention purged)."""
    if len(samples) < 2:
        return None
    now = time.time()
    window_start = now - 86400
    baseline = None
    for sample in samples:
        ts = float(sample.get("ts") or 0)
        if ts < window_start:
            baseline = sample
        elif baseline is None:
            baseline = sample

    if baseline is None:
        baseline = samples[0]
    latest = samples[-1]
    dt_days = (float(latest.get("ts") or 0) - float(baseline.get("ts") or 0)) / 86400.0
    if dt_days < 0.05:
        return None

    b0 = int((baseline.get("categories") or {}).get(category) or 0)
    b1 = int((latest.get("categories") or {}).get(category) or 0)
    return round((b1 - b0) / (1024**3) / dt_days, 3)


def _measure_ingest_bytes(root: str, hours: float = 24.0) -> Tuple[int, int]:
    """Bytes and file count with mtime inside the last *hours*."""
    if not root or not os.path.isdir(root):
        return 0, 0
    cutoff = time.time() - hours * 3600
    total = 0
    count = 0
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _ALL_MEDIA_EXTS:
                continue
            fp = os.path.join(dirpath, fname)
            try:
                if os.path.getmtime(fp) < cutoff:
                    continue
                total += os.path.getsize(fp)
                count += 1
            except OSError:
                continue
    return total, count


def _cached_ingest(project_root: str, category: str, root: str, hours: float = 24.0) -> Tuple[float, int, str]:
    """Return (gb_per_day, files_per_day, source)."""
    cache_path = _ingest_cache_path(project_root)
    cache = _load_json(cache_path)
    entries = cache.setdefault("categories", {})
    entry = entries.get(category) or {}
    now = time.time()
    if entry and (now - float(entry.get("ts") or 0)) < _INGEST_CACHE_TTL_S:
        return (
            float(entry.get("gb_per_day") or 0),
            int(entry.get("files_per_day") or 0),
            str(entry.get("source") or "cached"),
        )

    total_b, count = _measure_ingest_bytes(root, hours=hours)
    gb_per_day = round((total_b / (1024**3)) * (24.0 / max(hours, 1)), 3)
    files_per_day = int(round(count * (24.0 / max(hours, 1))))
    entries[category] = {
        "ts": now,
        "gb_per_day": gb_per_day,
        "files_per_day": files_per_day,
        "source": "measured",
    }
    cache["categories"] = entries
    _save_json(cache_path, cache)
    return gb_per_day, files_per_day, "measured"


def _implied_gb_per_day(bytes_used: int, local_days: int) -> float:
    if bytes_used <= 0 or local_days <= 0:
        return 0.0
    return round((bytes_used / (1024**3)) / local_days, 3)


def get_detection_event_rates(db_path: str, days: int = 7) -> Dict[str, Any]:
    """Daily detection counts from analytics DB."""
    if not db_path or not os.path.isfile(db_path):
        return {"events_per_day": 0, "daily_counts": [], "source": "unavailable"}

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DATE(timestamp) AS day, COUNT(*) AS cnt
                FROM events
                WHERE timestamp >= datetime('now', ?)
                GROUP BY DATE(timestamp)
                ORDER BY day DESC
                """,
                (f"-{int(days)} days",),
            )
            rows = cursor.fetchall()
    except Exception as exc:
        logger.debug("Detection rate query failed: %s", exc)
        return {"events_per_day": 0, "daily_counts": [], "source": "error"}

    daily = [{"date": r[0], "count": int(r[1])} for r in rows]
    if not daily:
        return {"events_per_day": 0, "daily_counts": [], "source": "empty"}

    counts = [d["count"] for d in daily]
    avg = sum(counts) / len(counts)
    return {
        "events_per_day": round(avg, 1),
        "events_today": daily[0]["count"] if daily else 0,
        "daily_counts": daily,
        "source": "database",
    }


def get_detection_rates_by_camera(
    db_path: str,
    days: int = 7,
    camera_names: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    """
    Rank cameras by detection volume + top classes.
    Used to recommend filtering or disabling noisy cameras.
    """
    if not db_path or not os.path.isfile(db_path):
        return {"cameras": [], "total_events": 0, "days": days, "source": "unavailable"}

    names = camera_names or {}
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT camera_id, COUNT(*) AS cnt
                FROM events
                WHERE timestamp >= datetime('now', ?)
                  AND training_label IS NULL
                GROUP BY camera_id
                ORDER BY cnt DESC
                """,
                (f"-{int(days)} days",),
            )
            cam_rows = cursor.fetchall()

            cursor.execute(
                """
                SELECT camera_id, class_name, COUNT(*) AS cnt
                FROM events
                WHERE timestamp >= datetime('now', ?)
                  AND training_label IS NULL
                GROUP BY camera_id, class_name
                ORDER BY camera_id, cnt DESC
                """,
                (f"-{int(days)} days",),
            )
            class_rows = cursor.fetchall()
    except Exception as exc:
        logger.debug("Per-camera detection rate query failed: %s", exc)
        return {"cameras": [], "total_events": 0, "days": days, "source": "error"}

    top_classes: Dict[int, List[Dict[str, Any]]] = {}
    for cam_id, class_name, cnt in class_rows:
        cid = int(cam_id) if cam_id is not None else -1
        bucket = top_classes.setdefault(cid, [])
        if len(bucket) >= 3:
            continue
        bucket.append({"class": class_name or "unknown", "count": int(cnt)})

    total = sum(int(r[1]) for r in cam_rows)
    cameras: List[Dict[str, Any]] = []
    for cam_id, cnt in cam_rows:
        cid = int(cam_id) if cam_id is not None else -1
        count = int(cnt)
        share = round(100.0 * count / total, 1) if total else 0.0
        per_day = round(count / max(days, 1), 1)
        label = names.get(cid) or f"Camera {cid + 1}"
        cameras.append(
            {
                "camera_id": cid,
                "camera_number": cid + 1,
                "label": label,
                "events": count,
                "events_per_day": per_day,
                "share_pct": share,
                "top_classes": top_classes.get(cid, []),
            }
        )

    return {
        "cameras": cameras,
        "total_events": total,
        "days": days,
        "source": "database",
    }


def _camera_display_names() -> Dict[int, str]:
    try:
        from basebuddy.modules.camera_profiles import get_profile_manager
        from basebuddy.modules.config import CAM_URLS

        manager = get_profile_manager()
        out: Dict[int, str] = {}
        for i, url in enumerate(CAM_URLS):
            if not url:
                continue
            profile = manager.get_profile(i)
            if profile and getattr(profile, "name", None):
                out[i] = profile.name
            else:
                out[i] = f"Camera {i + 1}"
        return out
    except Exception:
        return {}


def build_category_rates(
    project_root: str,
    source_dirs: Dict[str, str],
    category_sizes: Dict[str, int],
    retention_policy: Dict[str, Dict[str, int]],
    db_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Per-category write rates (GB/day, files/day) using measured ingest, history, and
  implied steady-state from current fill level.
    """
    history = _load_json(_history_path(project_root))
    samples: List[dict] = history.get("samples") or []
    record_size_sample(project_root, category_sizes)

    det_stats = get_detection_event_rates(db_path or "", days=7)
    cam_stats = get_detection_rates_by_camera(
        db_path or "", days=7, camera_names=_camera_display_names()
    )

    rates: Dict[str, Dict[str, Any]] = {}
    for category, root in source_dirs.items():
        cfg = retention_policy.get(category) or {}
        local_days = int(cfg.get("local_days") or 0)
        used_b = int(category_sizes.get(category) or 0)

        hist_rate = _rates_from_history(samples, category)
        ingest_gb, ingest_files, ingest_src = _cached_ingest(project_root, category, root)
        implied_gb = _implied_gb_per_day(used_b, local_days)

        # Prefer direct ingest measurement; fall back to implied fill rate.
        if ingest_gb > 0:
            gb_per_day = ingest_gb
            rate_source = ingest_src
        elif implied_gb > 0:
            gb_per_day = implied_gb
            rate_source = "implied"
        elif hist_rate is not None and hist_rate > 0:
            gb_per_day = hist_rate
            rate_source = "history"
        else:
            gb_per_day = 0.0
            rate_source = "none"

        files_per_day = ingest_files if ingest_files > 0 else 0
        row: Dict[str, Any] = {
            "gb_per_day": gb_per_day,
            "files_per_day": files_per_day,
            "source": rate_source,
            "local_days": local_days,
            "steady_state_gb": round(gb_per_day * local_days, 2) if local_days > 0 else 0.0,
        }

        if category == "detections" and det_stats.get("events_per_day"):
            row["events_per_day"] = det_stats["events_per_day"]
            media_gb = (used_b + int(category_sizes.get("video_thumbs") or 0)) / (1024**3)
            ev = float(det_stats["events_per_day"])
            if ev > 0 and media_gb > 0:
                row["mb_per_event"] = round((media_gb * 1024) / ev, 2)

            # Attach share of detection storage attributed to each camera.
            for cam in cam_stats.get("cameras") or []:
                cam["est_gb_per_day"] = round(
                    gb_per_day * (float(cam.get("share_pct") or 0) / 100.0), 3
                )
                cam["est_steady_gb"] = round(
                    cam["est_gb_per_day"] * local_days, 2
                ) if local_days > 0 else 0.0

        if category == "stills" and files_per_day > 0:
            row["stills_per_day"] = files_per_day

        rates[category] = row

    rates["_detection_db"] = det_stats
    rates["_cameras"] = cam_stats
    return rates
