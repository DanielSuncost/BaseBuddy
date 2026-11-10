"""Stitch event MP4 clips with overlap time-range dedupe."""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _ffprobe_duration(path: str) -> Optional[float]:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        ).strip()
        return float(out) if out else None
    except Exception as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None


def _resolve_clip(path: str) -> Optional[str]:
    from basebuddy.core.services.media_paths import url_to_filesystem

    if not path:
        return None
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    fs = url_to_filesystem(path)
    if fs:
        return fs
    # Relative RECORD_ROOT paths as stored by EventClipBuffer
    from basebuddy.modules.config import RECORD_ROOT
    from basebuddy.core.paths import abs_data_path

    candidate = os.path.join(abs_data_path(RECORD_ROOT), path)
    if os.path.isfile(candidate):
        return candidate
    if os.path.isfile(path):
        return os.path.abspath(path)
    return None


def plan_stitch(
    sessions: List[Dict[str, Any]],
    pre_s: float,
    post_s: float,
) -> List[Dict[str, Any]]:
    """Build a trim plan: each item has path, trim_start, duration_used, session_id."""
    items: List[Tuple[Dict[str, Any], str, float, float, float]] = []
    for s in sessions:
        fs = _resolve_clip(s.get("clip_path") or "")
        if not fs:
            continue
        dur = _ffprobe_duration(fs)
        if dur is None or dur < 0.2:
            continue
        t0 = float(s.get("clip_t0") or (float(s["started_at"]) - pre_s))
        # Prefer measured duration over estimated end so trim math stays consistent
        items.append((s, fs, t0, dur, t0 + dur))

    items.sort(key=lambda x: x[2])
    plan: List[Dict[str, Any]] = []
    cursor: Optional[float] = None
    for s, fs, t0, dur, t1 in items:
        if cursor is None:
            plan.append({
                "session_id": s["id"],
                "path": fs,
                "trim_start": 0.0,
                "trim_duration": dur,
                "t0": t0,
                "t1": t1,
            })
            cursor = t1
            continue
        if t1 <= cursor + 0.05:
            # Fully covered by prior clip(s)
            continue
        if t0 < cursor:
            trim = cursor - t0
            remain = dur - trim
            if remain < 0.15:
                continue
            plan.append({
                "session_id": s["id"],
                "path": fs,
                "trim_start": trim,
                "trim_duration": remain,
                "t0": cursor,
                "t1": t1,
            })
            cursor = t1
        else:
            plan.append({
                "session_id": s["id"],
                "path": fs,
                "trim_start": 0.0,
                "trim_duration": dur,
                "t0": t0,
                "t1": t1,
            })
            cursor = t1
    return plan


def stitch_clips(
    sessions: List[Dict[str, Any]],
    pre_s: float,
    post_s: float,
    camera_id: int,
) -> Optional[Dict[str, Any]]:
    """Write a stitched MP4; returns path + plan metadata."""
    plan = plan_stitch(sessions, pre_s, post_s)
    if len(plan) < 1:
        return None

    from basebuddy.modules.config import RECORD_ROOT
    from basebuddy.core.paths import abs_data_path

    day = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(
        abs_data_path(RECORD_ROOT), f"cam{int(camera_id) + 1}", "events", day, "stitched"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_name = f"stitch_{uuid.uuid4().hex[:12]}.mp4"
    out_path = os.path.join(out_dir, out_name)

    if len(plan) == 1:
        # Single trim or passthrough via ffmpeg re-mux/trim
        p = plan[0]
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{p['trim_start']:.3f}",
            "-t", f"{p['trim_duration']:.3f}",
            "-i", p["path"],
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-an",
            "-movflags", "+faststart",
            out_path,
        ]
    else:
        # filter_complex trim + concat (video only)
        inputs: List[str] = []
        filters: List[str] = []
        labels: List[str] = []
        for i, p in enumerate(plan):
            inputs.extend(["-i", p["path"]])
            lab = f"v{i}"
            filters.append(
                f"[{i}:v]trim=start={p['trim_start']:.3f}:duration={p['trim_duration']:.3f},"
                f"setpts=PTS-STARTPTS,"
                f"scale=640:360:force_original_aspect_ratio=decrease,"
                f"pad=640:360:(ow-iw)/2:(oh-ih)/2,"
                f"fps=14,format=yuv420p[{lab}]"
            )
            labels.append(f"[{lab}]")
        filter_complex = ";".join(filters) + ";" + "".join(labels) + f"concat=n={len(plan)}:v=1:a=0[outv]"
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-movflags", "+faststart",
            out_path,
        ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0 or not os.path.isfile(out_path):
            logger.warning("Stitch ffmpeg failed: %s", (proc.stderr or "")[-800:])
            return None
    except Exception as exc:
        logger.warning("Stitch failed: %s", exc)
        return None

    rel = os.path.relpath(out_path, abs_data_path(RECORD_ROOT)).replace("\\", "/")
    public = "/recordings/" + rel
    return {
        "path": out_path,
        "relative_path": rel,
        "url": public,
        "plan": [
            {
                "session_id": p["session_id"],
                "trim_start": p["trim_start"],
                "trim_duration": p["trim_duration"],
                "t0": p["t0"],
                "t1": p["t1"],
            }
            for p in plan
        ],
        "clip_count": len(plan),
    }
