"""Disk fill forecasts and user-facing storage warnings."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from basebuddy.core.retention_defaults import RETENTION_SERVICE_LABELS


def _pick_rate_gb_day(rate_row: Dict[str, Any]) -> float:
    return float(rate_row.get("gb_per_day") or 0)


def build_storage_forecast(
    *,
    category_sizes: Dict[str, int],
    category_rates: Dict[str, Dict[str, Any]],
    retention_policy: Dict[str, Dict[str, int]],
    system_disk: Dict[str, float],
    camera_count: int = 0,
) -> Dict[str, Any]:
    """
    Forecast steady-state usage and emit warnings when retention windows exceed disk
    capacity or free space is critically low.
    """
    free_gb = float(system_disk.get("free_gb") or 0)
    total_gb = float(system_disk.get("total_gb") or 0)
    used_pct = float(system_disk.get("percent_used") or 0)

    rows: List[Dict[str, Any]] = []
    steady_total_gb = 0.0
    ingest_total_gb_day = 0.0

    for key, label in RETENTION_SERVICE_LABELS.items():
        cfg = retention_policy.get(key) or {}
        local_days = int(cfg.get("local_days") or 0)
        used_gb = round(int(category_sizes.get(key) or 0) / (1024**3), 2)
        rate_row = category_rates.get(key) or {}
        gb_day = _pick_rate_gb_day(rate_row)
        steady_gb = float(rate_row.get("steady_state_gb") or (gb_day * local_days if local_days else 0))

        ingest_total_gb_day += gb_day
        if local_days > 0:
            steady_total_gb += steady_gb

        detail_parts: List[str] = []
        if gb_day > 0:
            detail_parts.append(f"{gb_day:.2f} GiB/day written")
        if rate_row.get("events_per_day"):
            detail_parts.append(f"{rate_row['events_per_day']:.0f} detections/day")
        if rate_row.get("stills_per_day"):
            detail_parts.append(f"{rate_row['stills_per_day']:,} stills/day")

        rows.append(
            {
                "id": key,
                "label": label,
                "used_gb": used_gb,
                "gb_per_day": round(gb_day, 3),
                "local_days": local_days,
                "steady_state_gb": round(steady_gb, 2),
                "rate_source": rate_row.get("source") or "none",
                "detail": " · ".join(detail_parts) if detail_parts else None,
            }
        )

    # Non-media headroom: everything else on the filesystem.
    local_media_gb = sum(int(v) for v in category_sizes.values()) / (1024**3)
    other_on_disk_gb = max(0.0, round((total_gb - free_gb) - local_media_gb, 1))

    projected_media_gb = round(steady_total_gb, 1)
    projected_disk_used_gb = round(other_on_disk_gb + projected_media_gb, 1)
    headroom_gb = round(total_gb - projected_disk_used_gb, 1) if total_gb else 0

    warnings: List[Dict[str, Any]] = []

    def add(level: str, title: str, message: str, action: Optional[str] = None) -> None:
        warnings.append({"level": level, "title": title, "message": message, "action": action})

    if total_gb > 0 and free_gb < 10:
        add(
            "critical",
            "Disk almost full",
            f"Only {free_gb:.1f} GiB free on this drive ({used_pct:.0f}% used). "
            "Retention deletes by age only — it will not free space until files expire.",
            "Shorten detection and still retention below, or enable archive to USB.",
        )
    elif total_gb > 0 and used_pct >= 90:
        add(
            "warning",
            "Disk usage high",
            f"Filesystem is {used_pct:.0f}% full ({free_gb:.1f} GiB free).",
            "Review write rates and retention windows.",
        )

    if projected_disk_used_gb > total_gb > 0:
        over = round(projected_disk_used_gb - total_gb, 1)
        add(
            "critical",
            "Retention policy exceeds disk size",
            f"At current write rates, BaseBuddy media alone needs ~{projected_media_gb} GiB "
            f"once buckets fill their retention windows — about {over} GiB more than this "
            f"{total_gb:.0f} GiB drive can hold (plus ~{other_on_disk_gb} GiB used by the OS and other apps).",
            "Lower detection/still days in the planner, raise AI confidence, or enable archive.",
        )
    elif headroom_gb < 20 and projected_media_gb > 0 and total_gb > 0:
        add(
            "warning",
            "Projected media usage is tight",
            f"Steady-state BaseBuddy media is estimated at ~{projected_media_gb} GiB with only "
            f"~{headroom_gb} GiB headroom on a {total_gb:.0f} GiB drive.",
            "Consider shorter retention for detections and timelapse stills.",
        )

    det = category_rates.get("detections") or {}
    det_row = next((r for r in rows if r["id"] == "detections"), None)
    if det_row and det_row["used_gb"] >= 20 and det_row["local_days"] > 0:
        # Age-based retention looks "broken" when the whole bucket is younger than local_days.
        add(
            "critical" if free_gb < 30 else "warning",
            "Detection gallery not shrinking",
            f"You have {det_row['used_gb']:.0f} GiB of detection images. Retention only deletes "
            f"files older than {det_row['local_days']} days — at ~{det_row['gb_per_day']:.1f} GiB/day "
            f"write rate the gallery stays near {det_row['steady_state_gb']:.0f} GiB and almost nothing "
            "is old enough to delete yet.",
            f"Lower detection local days to 3–5, or rely on disk-pressure cleanup "
            f"(auto-deletes oldest files when free space drops below the minimum).",
        )
    if det_row and det_row["steady_state_gb"] >= 40:
        ev = det.get("events_per_day")
        ev_txt = f" ({ev:.0f} detections/day)" if ev else ""
        add(
            "warning",
            "Detection gallery is the largest growth driver",
            f"Detection images are writing ~{det_row['gb_per_day']:.2f} GiB/day{ev_txt}. "
            f"At {det_row['local_days']} days retention that stabilizes around "
            f"{det_row['steady_state_gb']:.0f} GiB.",
            "Lower detection local days to 3–7, or increase AI_CONF to reduce saved events.",
        )

    still_row = next((r for r in rows if r["id"] == "stills"), None)
    if still_row and still_row["steady_state_gb"] >= 30:
        add(
            "warning",
            "Timelapse stills filling disk",
            f"Timelapse captures are ~{still_row['gb_per_day']:.2f} GiB/day "
            f"(~{category_rates.get('stills', {}).get('stills_per_day', '?')} files/day). "
            f"At {still_row['local_days']} days that is ~{still_row['steady_state_gb']:.0f} GiB.",
            "Shorten still retention or reduce capture frequency on the Timelapse page.",
        )

    if camera_count >= 8 and ingest_total_gb_day > 2:
        add(
            "info",
            f"{camera_count} cameras writing media",
            f"Combined measured ingest is ~{ingest_total_gb_day:.1f} GiB/day across all categories. "
            "Retention is time-based: disk stays full until files age out.",
            None,
        )

    days_until_full = None
    if ingest_total_gb_day > 0.05 and free_gb > 0:
        days_until_full = round(free_gb / ingest_total_gb_day, 1)
        if days_until_full < 14:
            add(
                "critical" if days_until_full < 7 else "warning",
                "Disk may fill before retention catches up",
                f"At ~{ingest_total_gb_day:.1f} GiB/day ingest and {free_gb:.1f} GiB free, "
                f"space could run out in ~{days_until_full} days unless retention shortens or archive runs.",
                "Enable archive, lower retention days, or free space manually.",
            )

    camera_rank = category_rates.get("_cameras") or {}
    camera_rows: List[Dict[str, Any]] = list(camera_rank.get("cameras") or [])
    recommendations: List[Dict[str, Any]] = []

    if camera_rows:
        top = camera_rows[0]
        if top.get("share_pct", 0) >= 25 and len(camera_rows) >= 2:
            classes = top.get("top_classes") or []
            class_txt = ", ".join(
                f"{c['class']} ({c['count']:,})" for c in classes[:3]
            ) or "mixed classes"
            msg = (
                f"{top['label']} accounts for {top['share_pct']:.0f}% of detections "
                f"(~{top['events_per_day']:.0f}/day"
            )
            if top.get("est_gb_per_day"):
                msg += f", ~{top['est_gb_per_day']:.1f} GiB/day of detection images"
            msg += f"). Top classes: {class_txt}."
            recommendations.append(
                {
                    "camera_id": top["camera_id"],
                    "camera_number": top["camera_number"],
                    "label": top["label"],
                    "kind": "filter_or_disable",
                    "severity": msg,
                    "actions": [
                        {
                            "label": "Raise class thresholds",
                            "href": "/config",
                            "detail": "Increase confidence for noisy classes on this camera",
                        },
                        {
                            "label": "Add ignore ROIs",
                            "href": "/config",
                            "detail": "Mask foliage, roads, or busy areas that spam detections",
                        },
                        {
                            "label": "Disable camera detection",
                            "href": "/config",
                            "detail": "Turn off AI for this camera if you don't need its events",
                        },
                    ],
                }
            )
            add(
                "warning",
                f"{top['label']} is driving most detections",
                msg,
                "Open Camera recommendations below — filter classes, add ignore zones, or disable detection on that camera.",
            )

        # Second camera if also heavy
        if len(camera_rows) >= 2 and camera_rows[1].get("share_pct", 0) >= 20:
            second = camera_rows[1]
            recommendations.append(
                {
                    "camera_id": second["camera_id"],
                    "camera_number": second["camera_number"],
                    "label": second["label"],
                    "kind": "filter",
                    "severity": (
                        f"{second['label']} is #2 at {second['share_pct']:.0f}% "
                        f"(~{second['events_per_day']:.0f} detections/day)."
                    ),
                    "actions": [
                        {
                            "label": "Tune this camera",
                            "href": "/config",
                            "detail": "Raise thresholds or add ignored ROIs",
                        }
                    ],
                }
            )

        # Dominated by a few cameras tip
        top3_share = sum(float(c.get("share_pct") or 0) for c in camera_rows[:3])
        if len(camera_rows) >= 4 and top3_share >= 70:
            add(
                "info",
                "A few cameras dominate detection volume",
                f"The top 3 cameras produce {top3_share:.0f}% of all detections. "
                "Filtering those has the biggest disk impact.",
                None,
            )

    return {
        "rows": rows,
        "steady_state_media_gb": round(steady_total_gb, 2),
        "ingest_gb_per_day": round(ingest_total_gb_day, 3),
        "projected_disk_used_gb": projected_disk_used_gb,
        "other_on_disk_gb": other_on_disk_gb,
        "headroom_gb": headroom_gb,
        "days_until_full": days_until_full,
        "camera_count": camera_count,
        "warnings": warnings,
        "camera_ranking": {
            "days": camera_rank.get("days", 7),
            "total_events": camera_rank.get("total_events", 0),
            "cameras": camera_rows,
            "recommendations": recommendations,
        },
    }
