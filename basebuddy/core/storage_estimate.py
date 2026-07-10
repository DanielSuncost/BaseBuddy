"""Estimate future storage use for retention / cloud planning."""
from typing import Any, Dict, List


# Planning constants (bytes)
DETECTION_EVENT_BYTES = 150 * 1024  # thumb + padded crop @ 1080p
STILL_JPEG_1080P = 350 * 1024
STILL_JPEG_4K = 1_200 * 1024
CLIP_EVENT_BYTES = 3 * 1024 * 1024  # hypothetical 15s event clip


def _gb(bytes_val: float) -> float:
    return round(bytes_val / (1024**3), 2)


def estimate_storage(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate storage for a scenario.

    Expected payload keys:
      cameras (int), retention_days (int), mode (detections|stills|mixed|clips|recordings)
      detections_per_cam_day, still_interval_sec, daylight_hours,
      resolution (1080p|720p|4k), still_cameras, detection_cameras
      recording_bitrate_mbps (for recordings mode)
    """
    cameras = max(1, int(payload.get("cameras") or 1))
    days = max(1, int(payload.get("retention_days") or 30))
    mode = (payload.get("mode") or "detections").lower()

    det_cams = int(payload.get("detection_cameras") or cameras)
    still_cams = int(payload.get("still_cameras") or cameras)
    detections_day = max(0, int(payload.get("detections_per_cam_day") or 100))
    interval = max(1, int(payload.get("still_interval_sec") or 60))
    daylight = max(1, float(payload.get("daylight_hours") or 14))

    res = (payload.get("resolution") or "1080p").lower()
    still_bytes = STILL_JPEG_4K if res == "4k" else STILL_JPEG_1080P
    if res == "720p":
        still_bytes = 180 * 1024

    lines: List[Dict[str, Any]] = []
    total_bytes = 0.0

    if mode in ("detections", "mixed"):
        b = det_cams * detections_day * DETECTION_EVENT_BYTES * days
        total_bytes += b
        lines.append(
            {
                "label": "Detection stills",
                "detail": f"{det_cams} cam × {detections_day}/day × {days}d",
                "gb": _gb(b),
            }
        )

    if mode in ("stills", "mixed"):
        stills_per_day = int((3600 * daylight) / interval)
        b = still_cams * stills_per_day * still_bytes * days
        total_bytes += b
        lines.append(
            {
                "label": "Timelapse stills",
                "detail": f"{still_cams} cam × every {interval}s × {daylight}h/day × {days}d",
                "gb": _gb(b),
            }
        )

    if mode == "clips":
        clips_day = max(0, int(payload.get("clips_per_cam_day") or detections_day))
        b = cameras * clips_day * CLIP_EVENT_BYTES * days
        total_bytes += b
        lines.append(
            {
                "label": "Event clips (planned feature)",
                "detail": f"{cameras} cam × {clips_day}/day × ~3 MB × {days}d",
                "gb": _gb(b),
            }
        )

    if mode == "recordings":
        mbps = max(0.5, float(payload.get("recording_bitrate_mbps") or 2.5))
        bytes_per_cam_day = mbps * 1_000_000 / 8 * 86400
        b = cameras * bytes_per_cam_day * days
        total_bytes += b
        lines.append(
            {
                "label": "Continuous recordings",
                "detail": f"{cameras} cam × {mbps} Mbps × {days}d",
                "gb": _gb(b),
            }
        )

    r2_monthly = round((total_bytes / (1024**3)) * 0.015, 2)
    s3_monthly = round((total_bytes / (1024**3)) * 0.023, 2)

    local_hint_gb = _gb(total_bytes * 0.15)  # suggest ~15% local buffer
    if local_hint_gb < 1:
        local_hint_gb = min(_gb(total_bytes), 64.0)

    return {
        "total_gb": _gb(total_bytes),
        "breakdown": lines,
        "cloud_cost_monthly_usd": {
            "r2": r2_monthly,
            "s3_standard": s3_monthly,
        },
        "suggested_local_buffer_gb": local_hint_gb,
        "mode": mode,
    }


def _recordings_bytes(cameras: int, days: int, mbps: float) -> float:
    if days <= 0:
        return 0.0
    bytes_per_cam_day = mbps * 1_000_000 / 8 * 86400
    return cameras * bytes_per_cam_day * days


def _detections_bytes(cameras: int, days: int, per_day: int) -> float:
    if days <= 0:
        return 0.0
    return cameras * per_day * DETECTION_EVENT_BYTES * days


def _stills_bytes(cameras: int, days: int, interval: int, daylight: float, still_bytes: int) -> float:
    if days <= 0:
        return 0.0
    stills_per_day = int((3600 * daylight) / max(1, interval))
    return cameras * stills_per_day * still_bytes * days


def _timelapse_output_bytes(cameras: int, days: int) -> float:
    """Rough: one ~50 MB MP4 per camera per day of timelapse output retained."""
    if days <= 0:
        return 0.0
    return cameras * days * 50 * 1024 * 1024


def estimate_retention_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Estimate local vs cloud storage from user-facing retention days.

    Payload keys:
      cameras, detections_per_cam_day, still_interval_sec, daylight_hours,
      recording_bitrate_mbps, resolution,
      local_days: {recordings, detections, stills, timelapse},
      cloud_days: {recordings, detections, stills, timelapse},
      pricing_tiers: optional list for tier suggestion
    """
    cameras = max(1, int(payload.get("cameras") or 1))
    per_day = max(0, int(payload.get("detections_per_cam_day") or 100))
    interval = max(1, int(payload.get("still_interval_sec") or 60))
    daylight = max(1, float(payload.get("daylight_hours") or 14))
    mbps = max(0.5, float(payload.get("recording_bitrate_mbps") or 2.5))
    res = (payload.get("resolution") or "1080p").lower()
    still_bytes = STILL_JPEG_4K if res == "4k" else STILL_JPEG_1080P
    if res == "720p":
        still_bytes = 180 * 1024

    local_days = payload.get("local_days") or {}
    cloud_days = payload.get("cloud_days") or {}

    categories = [
        ("recordings", "Video recordings", _recordings_bytes),
        ("detections", "Detection gallery", _detections_bytes),
        ("stills", "Timelapse captures", _stills_bytes),
        ("timelapse", "Timelapse videos", _timelapse_output_bytes),
    ]

    local_lines = []
    cloud_lines = []
    local_total = 0.0
    cloud_total = 0.0

    for key, label, fn in categories:
        ld = max(0, int(local_days.get(key) or 0))
        cd = max(0, int(cloud_days.get(key) or 0))
        if key == "recordings":
            lb = fn(cameras, ld, mbps)
            cb = fn(cameras, cd, mbps)
        elif key == "detections":
            lb = fn(cameras, ld, per_day)
            cb = fn(cameras, cd, per_day)
        elif key == "stills":
            lb = fn(cameras, ld, interval, daylight, still_bytes)
            cb = fn(cameras, cd, interval, daylight, still_bytes)
        else:
            lb = fn(cameras, ld)
            cb = fn(cameras, cd)
        local_total += lb
        cloud_total += cb
        if ld > 0:
            local_lines.append({"id": key, "label": label, "days": ld, "gb": _gb(lb)})
        if cd > 0:
            cloud_lines.append({"id": key, "label": label, "days": cd, "gb": _gb(cb)})

    max_cloud_days = max([int(cloud_days.get(k) or 0) for k, _, _ in categories] + [0])
    tier = suggest_premium_tier(cloud_total / (1024**3), max_cloud_days, payload.get("pricing_tiers") or [])

    return {
        "local_total_gb": _gb(local_total),
        "cloud_total_gb": _gb(cloud_total),
        "local_breakdown": local_lines,
        "cloud_breakdown": cloud_lines,
        "max_cloud_days": max_cloud_days,
        "suggested_tier": tier,
        "still_interval_sec": interval,
        "cameras": cameras,
    }


def suggest_premium_tier(cloud_gb: float, max_cloud_days: int, tiers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick smallest tier that fits estimated cloud GB and buffer days."""
    if not tiers:
        return {}
    ordered = sorted(tiers, key=lambda t: t.get("cloud_storage_gb") or t.get("storage_gb") or 0)
    for tier in ordered:
        cap = tier.get("cloud_storage_gb") or tier.get("storage_gb") or 0
        buf = tier.get("cloud_buffer_days") or 30
        if cap >= cloud_gb and buf >= max_cloud_days:
            return {
                "id": tier.get("id"),
                "label": tier.get("label"),
                "cloud_storage_gb": cap,
                "cloud_buffer_days": buf,
                "storage_only_usd": tier.get("storage_only_usd"),
                "with_inference_usd": tier.get("with_inference_usd"),
                "fits": True,
            }
    last = ordered[-1]
    cap = last.get("cloud_storage_gb") or last.get("storage_gb") or 0
    return {
        "id": last.get("id"),
        "label": last.get("label"),
        "cloud_storage_gb": cap,
        "cloud_buffer_days": last.get("cloud_buffer_days") or 30,
        "storage_only_usd": last.get("storage_only_usd"),
        "with_inference_usd": last.get("with_inference_usd"),
        "fits": False,
        "over_by_gb": round(max(0, cloud_gb - cap), 1),
    }
