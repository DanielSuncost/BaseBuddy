"""
Measure local disk usage for BaseBuddy media directories.
"""
import os
import shutil
from typing import Dict, List, Optional, Tuple

USAGE_BREAKDOWN_ORDER = (
    "recordings",
    "detections",
    "stills",
    "timelapse",
    "video_thumbs",
    "recording_thumbs",
)


def _walk_size_bytes(root: str) -> int:
    total = 0
    if not root or not os.path.isdir(root):
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def category_sizes(source_dirs: Dict[str, str]) -> Dict[str, int]:
    """Bytes per archive label (recordings, stills, media, ...)."""
    out: Dict[str, int] = {}
    for label, path in source_dirs.items():
        out[label] = _walk_size_bytes(path)
    return out


def total_local_media_bytes(source_dirs: Dict[str, str]) -> int:
    return sum(category_sizes(source_dirs).values())


def disk_usage_path(path: str) -> Tuple[bool, Dict[str, float]]:
    """
    Return (ok, {total_gb, used_gb, free_gb, percent_used}) for filesystem
    hosting *path*, or (False, {}) if unavailable.
    """
    try:
        if not path or not os.path.exists(path):
            return False, {}
        usage = shutil.disk_usage(path)
        total = float(usage.total)
        free = float(usage.free)
        used = total - free
        pct = round(100.0 * used / total, 1) if total else 0.0
        gb = 1024**3
        return True, {
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
            "percent_used": pct,
        }
    except OSError:
        return False, {}


def quota_status(quota_gb: float, used_bytes: int) -> Dict[str, object]:
    """quota_gb 0 or negative = disabled."""
    if quota_gb is None or quota_gb <= 0:
        return {
            "enabled": False,
            "quota_gb": 0.0,
            "used_gb": round(used_bytes / (1024**3), 2),
            "over": False,
            "percent_of_quota": None,
        }
    used_gb = used_bytes / (1024**3)
    return {
        "enabled": True,
        "quota_gb": round(quota_gb, 2),
        "used_gb": round(used_gb, 2),
        "over": used_gb > quota_gb,
        "percent_of_quota": round(100.0 * used_gb / quota_gb, 1) if quota_gb > 0 else None,
    }


def cloud_category_sizes(remote_backend) -> Dict[str, int]:
    """Bytes per retention category in the active remote backend (S3/R2/managed)."""
    if remote_backend is None or not getattr(remote_backend, "is_active", False):
        return {}
    list_fn = getattr(remote_backend, "list_objects", None)
    if not list_fn:
        return {}
    out: Dict[str, int] = {}
    try:
        for obj in list_fn("") or []:
            cat = (obj.get("category") or "other").strip()
            out[cat] = out.get(cat, 0) + int(obj.get("size") or 0)
    except Exception:
        return {}
    return out


def build_usage_breakdown(
    local_sizes: Dict[str, int],
    cloud_sizes: Dict[str, int],
    policy: Dict,
    labels: Dict[str, str],
    *,
    remote_active: bool,
) -> Dict[str, object]:
    """Structured local vs cloud usage rows for the storage UI."""
    rows: List[Dict[str, object]] = []
    local_total = 0
    cloud_total = 0

    for key in USAGE_BREAKDOWN_ORDER:
        local_b = int(local_sizes.get(key) or 0)
        local_total += local_b
        cloud_b: Optional[int] = None
        if remote_active:
            cloud_b = int(cloud_sizes.get(key) or 0)
            cloud_total += cloud_b

        cfg = (policy or {}).get(key) or {}
        rows.append(
            {
                "id": key,
                "label": labels.get(key, key.replace("_", " ").title()),
                "local_bytes": local_b,
                "local_gb": round(local_b / (1024**3), 2),
                "cloud_bytes": cloud_b,
                "cloud_gb": round(cloud_b / (1024**3), 2) if cloud_b is not None else None,
                "local_days": int(cfg.get("local_days") or 0),
                "remote_days": int(cfg.get("remote_days") or 0),
            }
        )

    return {
        "rows": rows,
        "local_total_gb": round(local_total / (1024**3), 2),
        "cloud_total_gb": round(cloud_total / (1024**3), 2) if remote_active else None,
        "cloud_active": remote_active,
        "cloud_backend": None,
    }
