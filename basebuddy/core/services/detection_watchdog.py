"""Decide when detection has stopped producing results.

The grabber watchdog and /health share these helpers so a stall is detected
and reported with the same rule.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

# Long enough for model load and a slow shared-GPU pass, short enough that a
# wedged worker is noticed before an operator has to restart the process.
DETECTION_STALE_S = 45.0
DETECTION_WATCHDOG_INTERVAL_S = 5.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def recently_fed(last_enqueue_ts: float, queue_depth: int, now: float, stale_after_s: float) -> bool:
    """True when capture is still handing frames to detection."""
    if queue_depth > 0:
        return True
    return last_enqueue_ts > 0 and (now - last_enqueue_ts) <= stale_after_s


def is_detection_stale(
    *,
    now: float,
    detection_enabled: bool,
    running: bool,
    worker_started_ts: float,
    last_detection_ts: float,
    last_frame_ts: float,
    last_restart_ts: float,
    last_enqueue_ts: float,
    queue_depth: int,
    stale_after_s: float = DETECTION_STALE_S,
) -> bool:
    """Detection is stale when frames keep arriving but results have stopped.

    A dead capture source is not a detection stall. Startup and the grace
    period after an in-process restart are not stalls either.
    """
    if not detection_enabled or not running:
        return False
    if worker_started_ts <= 0 or (now - worker_started_ts) < stale_after_s:
        return False
    if last_restart_ts > 0 and (now - last_restart_ts) < stale_after_s:
        return False
    if last_frame_ts <= 0 or (now - last_frame_ts) > stale_after_s:
        return False
    if not recently_fed(last_enqueue_ts, queue_depth, now, stale_after_s):
        return False
    if last_detection_ts <= 0:
        return True
    return (now - last_detection_ts) > stale_after_s


def result_age_s(last_detection_ts: float, worker_started_ts: float, now: float) -> Optional[float]:
    if last_detection_ts > 0:
        return now - last_detection_ts
    if worker_started_ts > 0:
        return now - worker_started_ts
    return None


def _queue_depth(grabber: Any) -> int:
    try:
        return len(getattr(grabber, "detection_queue", []) or [])
    except Exception:
        return 0


def camera_detection_status(
    cam_id: int,
    grabber: Any,
    now: float,
    stale_after_s: float = DETECTION_STALE_S,
) -> Dict[str, Any]:
    enabled = bool(getattr(grabber, "detection_enabled", False))
    running = bool(getattr(grabber, "running", False))
    last = _f(getattr(grabber, "last_detection_ts", 0))
    started = _f(getattr(grabber, "_detection_worker_started_ts", 0))
    last_frame = _f(getattr(grabber, "_last_successful_read", 0))
    last_restart = _f(getattr(grabber, "_last_detection_restart_ts", 0))
    last_enqueue = _f(getattr(grabber, "_last_detection_enqueue_ts", 0))
    depth = _queue_depth(grabber)
    stale = is_detection_stale(
        now=now,
        detection_enabled=enabled,
        running=running,
        worker_started_ts=started,
        last_detection_ts=last,
        last_frame_ts=last_frame,
        last_restart_ts=last_restart,
        last_enqueue_ts=last_enqueue,
        queue_depth=depth,
        stale_after_s=stale_after_s,
    )
    age = result_age_s(last, started, now) if (enabled and running) else (result_age_s(last, 0.0, now) if last > 0 else None)
    return {
        "camera_id": int(cam_id),
        "detection_enabled": enabled,
        "running": running,
        "last_detection_ts": last if last > 0 else None,
        "last_detection_age_s": round(age, 1) if age is not None else None,
        "stale": stale,
    }


def detection_health(
    grabbers: Optional[Mapping[Any, Any]],
    now: Optional[float] = None,
    stale_after_s: float = DETECTION_STALE_S,
) -> Dict[str, Any]:
    """Snapshot used by /health. ``last_detection_*`` describes the worst camera."""
    now = time.time() if now is None else now
    cameras: List[Dict[str, Any]] = []
    if grabbers:
        for cam_id in sorted(grabbers, key=lambda key: key):
            cameras.append(camera_detection_status(int(cam_id), grabbers[cam_id], now, stale_after_s))

    # Prefer a stale camera over a merely slow one, so the summary matches the flag.
    watched = [
        c for c in cameras
        if c["detection_enabled"] and c["running"] and (c["stale"] or c["last_detection_ts"])
    ]
    worst = max(watched, key=lambda c: (c["stale"], c["last_detection_age_s"] or 0)) if watched else None
    return {
        "stale": any(c["stale"] for c in cameras),
        "stale_after_s": stale_after_s,
        "last_detection_ts": None if worst is None else worst["last_detection_ts"],
        "last_detection_age_s": None if worst is None else worst["last_detection_age_s"],
        "cameras": cameras,
    }
