"""Several cameras on one plant.

Each monitor stays an angle: its own mask, stills, growth series, and charts.
Angles that share ``subject_id`` are read together for alerts and for the
qualitative agent. Playback is time-locked by nearest sample, so cameras with
different intervals still sit on one clock.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

# Same decline bar the color trend uses for G_norm.
_MEAN_RATE_ALERT = -0.008
# A frame further than this from the playhead is a gap, not a hold.
DEFAULT_MAX_GAP_S = 2 * 3600


def new_subject_id() -> str:
    return "subject-" + uuid.uuid4().hex[:8]


def subject_key(monitor: dict) -> str:
    sid = (monitor or {}).get("subject_id") or ""
    if str(sid).strip():
        return str(sid).strip()
    return "solo:" + str((monitor or {}).get("id") or "")


def group_monitors(monitors: List[dict]) -> List[List[dict]]:
    """Stable groups. Ungrouped monitors stay a group of one."""
    order: List[str] = []
    buckets: Dict[str, List[dict]] = {}
    for monitor in monitors or []:
        key = subject_key(monitor)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(monitor)
    return [buckets[k] for k in order]


def subject_title(monitors: List[dict]) -> str:
    named = [str(m.get("subject_name") or "").strip() for m in monitors if m.get("subject_name")]
    if named:
        return named[0]
    names = [str(m.get("name") or "Plant") for m in monitors]
    return min(names, key=len) if names else "Plant"


def _mean(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return sum(vals) / float(len(vals))


def angle_snapshot(monitor: dict) -> Dict[str, Any]:
    """One camera's latest numbers, shaped for the combined decision."""
    color = (monitor or {}).get("last_color_sample") or {}
    trends = (monitor or {}).get("trends") or {}
    analysis = (monitor or {}).get("last_analysis") or {}
    g_norm = color.get("G_norm")
    if g_norm is None:
        g_norm = trends.get("G_norm_latest")
    return {
        "monitor_id": monitor.get("id"),
        "name": monitor.get("name") or "Angle",
        "camera_id": monitor.get("camera_id"),
        "species_hint": monitor.get("species_hint") or "",
        "g_norm": g_norm,
        "g_rel": color.get("G_rel") if color.get("G_rel") is not None else color.get("greenness"),
        "coverage": color.get("coverage"),
        "plant_pixels": color.get("plant_pixels"),
        "sampled_at": color.get("sampled_at"),
        "rate_per_day": trends.get("greenness_rate_per_day"),
        "coverage_rate_per_day": trends.get("coverage_rate_per_day"),
        "declining": bool(trends.get("declining_greenness")),
        "trend_metric": trends.get("trend_metric"),
        "health_score": analysis.get("health_score"),
        "summary": ((analysis.get("result") or {}) if isinstance(analysis.get("result"), dict) else {}).get("summary"),
        "sample_count": monitor.get("sample_count"),
    }


def combine_angles(angles: List[dict]) -> Dict[str, Any]:
    """Decide an alert from the set of angles, without erasing a disagreement.

    One camera alerts on its own trend.
    Two or more alert when the mean greenness rate is falling, or when every
    angle that has a trend is declining. One camera falling while another is
    not is a split: shown to the user, not treated as a plant-wide alert.
    """
    angles = list(angles or [])
    n = len(angles)
    rates = [float(a["rate_per_day"]) for a in angles if a.get("rate_per_day") is not None]
    gnorms = [float(a["g_norm"]) for a in angles if a.get("g_norm") is not None]
    covers = [float(a["coverage"]) for a in angles if a.get("coverage") is not None]
    declining = [a for a in angles if a.get("declining")]
    mean_rate = _mean(rates)
    mean_g = _mean(gnorms)
    mean_cover = _mean(covers)
    mean_decline = mean_rate is not None and mean_rate < _MEAN_RATE_ALERT and n >= 2
    all_decline = n >= 2 and len(declining) == n
    split = n >= 2 and 0 < len(declining) < n

    if n <= 1:
        alert = bool(declining)
        reason = "This camera's greenness is declining." if alert else ""
        basis = "single"
    elif all_decline or (mean_decline and not split):
        alert = True
        if all_decline:
            reason = "Every angle agrees greenness is declining."
            basis = "unanimous"
        else:
            reason = "Average greenness across angles is falling."
            basis = "mean"
    else:
        alert = False
        basis = "split" if split else "steady"
        reason = (
            "Angles disagree — open each camera before acting."
            if split else ""
        )

    return {
        "angle_count": n,
        "mean_g_norm": round(mean_g, 4) if mean_g is not None else None,
        "mean_coverage": round(mean_cover, 4) if mean_cover is not None else None,
        "mean_rate_per_day": round(mean_rate, 5) if mean_rate is not None else None,
        "declining_angles": [a.get("monitor_id") for a in declining],
        "alert": alert,
        "alert_basis": basis,
        "split": split,
        "reason": reason,
    }


def build_agent_brief(monitors: List[dict]) -> Dict[str, Any]:
    """Formal quantitative packet sent with the angle photos."""
    angles = [angle_snapshot(m) for m in monitors]
    combined = combine_angles(angles)
    times = [a["sampled_at"] for a in angles if a.get("sampled_at")]
    species = ""
    for m in monitors:
        if m.get("species_hint"):
            species = str(m["species_hint"])
            break
    return {
        "version": 1,
        "plant": {
            "name": subject_title(monitors),
            "species_hint": species,
            "subject_id": (monitors[0].get("subject_id") if monitors else "") or "",
            "angle_count": len(angles),
        },
        "instruction": (
            "This is one plant seen from separate fixed cameras. "
            "Judge health across the time window, not from a single frame. "
            "Keep each angle's reading. Agree when the cameras agree. "
            "If they disagree, name the camera that shows stress and do not average it away. "
            "Recommendations must be things a person can do in the next day or two."
        ),
        "window": {
            "latest_sample_at": max(times) if times else None,
            "earliest_latest_sample_at": min(times) if times else None,
        },
        "angles": angles,
        "combined": combined,
    }


def nearest_frame(
    frames: List[dict],
    at: float,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> Optional[dict]:
    """Frame closest to *at*. None when the camera has nothing inside the gap."""
    best = None
    best_dt = None
    for frame in frames or []:
        ts = frame.get("sampled_at")
        if ts is None:
            continue
        dt = abs(float(ts) - float(at))
        if best_dt is None or dt < best_dt:
            best = frame
            best_dt = dt
    if best is None or best_dt is None or best_dt > float(max_gap_s):
        return None
    return best


def rename_updates(
    monitor: dict,
    members: List[dict],
    *,
    plant_name: Optional[str] = None,
    angle_name: Optional[str] = None,
) -> List[dict]:
    """Field updates for a plant name and the selected camera's angle name.

    A solo monitor's plant name is its own name. A grouped plant's name is
    ``subject_name`` on every angle. The angle name is that camera's monitor name.
    """
    if not monitor:
        return []
    grouped = len(members or []) > 1 or bool(str((monitor or {}).get("subject_id") or "").strip())
    updates: Dict[str, Dict[str, str]] = {}

    def put(mid: str, **fields: str) -> None:
        row = updates.setdefault(str(mid), {"id": str(mid)})
        row.update(fields)

    if plant_name is not None:
        title = str(plant_name).strip()
        if not title:
            raise ValueError("Plant name cannot be empty")
        if grouped:
            for member in members or [monitor]:
                put(member["id"], subject_name=title)
        else:
            put(monitor["id"], name=title)
    if angle_name is not None and grouped:
        label = str(angle_name).strip()
        if not label:
            raise ValueError("Angle name cannot be empty")
        put(monitor["id"], name=label)
    return list(updates.values())


def link_angles(monitor_ids: List[str], name: Optional[str] = None) -> Dict[str, Any]:
    from basebuddy.plugins.plant_health.config import get_monitor, update_monitor

    found = []
    for mid in monitor_ids or []:
        monitor = get_monitor(str(mid))
        if monitor:
            found.append(monitor)
    if len(found) < 2:
        return {"ok": False, "error": "Choose at least two cameras of the same plant"}
    sid = next((m.get("subject_id") for m in found if str(m.get("subject_id") or "").strip()), None)
    if not sid:
        sid = new_subject_id()
    title = (name or "").strip() or subject_title(found)
    for monitor in found:
        update_monitor(monitor["id"], {"subject_id": sid, "subject_name": title})
    return {
        "ok": True,
        "subject_id": sid,
        "subject_name": title,
        "monitor_ids": [m["id"] for m in found],
    }


def unlink_angle(monitor_id: str) -> Dict[str, Any]:
    from basebuddy.plugins.plant_health.config import get_monitor, list_monitors, update_monitor

    monitor = get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "error": "not found"}
    sid = str(monitor.get("subject_id") or "").strip()
    update_monitor(monitor_id, {"subject_id": "", "subject_name": ""})
    if sid:
        left = [m for m in list_monitors() if str(m.get("subject_id") or "") == sid]
        if len(left) < 2:
            for other in left:
                update_monitor(other["id"], {"subject_id": "", "subject_name": ""})
    return {"ok": True, "monitor_id": monitor_id}


def align_playback(
    angles: List[dict],
    *,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    max_points: int = 360,
) -> Dict[str, Any]:
    """Shared clock. Each tick holds the nearest frame from each camera.

    ``angles`` items: ``{"monitor_id", "name", "camera_id", "frames": [{sampled_at, ...}]}``.
    """
    times = sorted({
        float(frame["sampled_at"])
        for angle in angles
        for frame in (angle.get("frames") or [])
        if frame.get("sampled_at") is not None
    })
    if len(times) > max_points:
        step = len(times) / float(max_points)
        times = [times[min(len(times) - 1, int(i * step))] for i in range(max_points)]
        # de-dupe after downsample
        deduped: List[float] = []
        for t in times:
            if not deduped or t != deduped[-1]:
                deduped.append(t)
        times = deduped

    out_angles = []
    for angle in angles:
        frames = angle.get("frames") or []
        column = []
        for t in times:
            hit = nearest_frame(frames, t, max_gap_s)
            if hit is None:
                column.append(None)
            else:
                column.append({
                    **hit,
                    "held_s": round(abs(float(hit["sampled_at"]) - t), 1),
                })
        out_angles.append({
            "monitor_id": angle.get("monitor_id"),
            "name": angle.get("name"),
            "camera_id": angle.get("camera_id"),
            "frames": column,
        })
    return {"times": times, "angles": out_angles, "max_gap_s": max_gap_s}
