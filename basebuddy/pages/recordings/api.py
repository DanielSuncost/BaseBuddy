"""
Recordings API endpoints and helpers.
"""
import hashlib
import logging
import os
import subprocess
from datetime import date, datetime, time, timedelta
from typing import Iterator, List, Optional, Tuple

from flask import jsonify

from basebuddy.pages.recordings import recordings_bp

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = (".mp4", ".avi", ".webm")

try:
    from basebuddy.modules.config import RECORD_ROOT
except ImportError:
    RECORD_ROOT = "recordings"

RANGE_PRESETS = {
    "today": 0,
    "7d": 6,
    "30d": 29,
}


def _abs_record_root() -> str:
    """Resolve RECORD_ROOT against repo root (not process cwd)."""
    from basebuddy.core.paths import get_repo_root
    from basebuddy.core.storage_paths import abs_under_project

    return abs_under_project(get_repo_root(), RECORD_ROOT)


def _video_thumbs_dir() -> str:
    """Directory for JPEG previews served at /static/video_thumbs/."""
    from basebuddy.core.paths import get_app_root

    thumb_dir = os.path.join(get_app_root(), "static", "video_thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    return thumb_dir


def _thumb_paths_for_video(file_path: str, record_root: str) -> tuple[str, str]:
    """Return (filesystem path, public URL) for a recording's thumbnail."""
    try:
        rel = os.path.relpath(os.path.abspath(file_path), record_root)
    except ValueError:
        rel = os.path.basename(file_path)
    rel = rel.replace("\\", "/")
    digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16]
    filename = f"thumb_{digest}.jpg"
    return os.path.join(_video_thumbs_dir(), filename), f"/static/video_thumbs/{filename}"


def _ffmpeg_thumbnail(video_path: str, output_path: str, time_offset: float = 1.0) -> bool:
    """Extract a frame using ffmpeg (best for libx264 MP4 segments)."""
    try:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(max(0.0, time_offset)),
            "-i",
            video_path,
            "-frames:v",
            "1",
            "-vf",
            "scale=320:-1",
            "-q:v",
            "4",
            output_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            logger.debug(
                "ffmpeg thumbnail failed for %s: %s",
                video_path,
                (result.stderr or b"").decode("utf-8", errors="replace")[:300],
            )
            return False
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0
    except FileNotFoundError:
        logger.debug("ffmpeg not available for thumbnail generation")
        return False
    except Exception as exc:
        logger.warning("ffmpeg thumbnail error for %s: %s", video_path, exc)
        return False


def _opencv_thumbnail(video_path: str, output_path: str, time_offset: float = 1.0) -> bool:
    """OpenCV fallback when ffmpeg is unavailable."""
    try:
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(time_offset * fps))
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return False

        height, width = frame.shape[:2]
        new_width = 320
        new_height = max(1, int(height * (new_width / max(width, 1))))
        frame = cv2.resize(frame, (new_width, new_height))
        return bool(cv2.imwrite(output_path, frame))
    except Exception as exc:
        logger.warning("OpenCV thumbnail error for %s: %s", video_path, exc)
        return False


def generate_video_thumbnail(video_path: str, output_path: str, time_offset: float = 1.0) -> bool:
    """Generate a JPEG thumbnail from a recording file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if _ffmpeg_thumbnail(video_path, output_path, time_offset=time_offset):
        return True
    return _opencv_thumbnail(video_path, output_path, time_offset=time_offset)


def _ensure_thumbnail(video_path: str, thumb_path: str, video_mtime: float) -> bool:
    """Create or refresh thumbnail when missing or older than the video."""
    if os.path.isfile(thumb_path):
        try:
            if os.path.getmtime(thumb_path) >= video_mtime:
                return True
        except OSError:
            pass
    return generate_video_thumbnail(video_path, thumb_path)


def _public_url_path_for_file(file_path: str) -> str:
    """Stable /recordings/... URL relative to RECORD_ROOT."""
    root = _abs_record_root()
    abs_file = os.path.abspath(file_path)
    try:
        rel = os.path.relpath(abs_file, root)
    except ValueError:
        rel = os.path.basename(abs_file)
    rel = rel.replace("\\", "/")
    if rel.startswith(".."):
        rel = os.path.basename(abs_file)
    return "/recordings/" + rel.lstrip("/")


def _discover_camera_ids(record_root: str) -> List[int]:
    camera_ids = []
    try:
        for entry in os.scandir(record_root):
            if not entry.is_dir():
                continue
            name = entry.name.lower()
            if name.startswith("cam") and name[3:].isdigit():
                camera_ids.append(int(name[3:]) - 1)
    except FileNotFoundError:
        return []
    return sorted(camera_ids)


def parse_date_range(
    range_preset: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    legacy_date: Optional[str] = None,
) -> Tuple[date, date, str]:
    """
    Resolve (start_date, end_date, preset_key).

    preset_key is one of: today, 7d, 30d, custom, day
    """
    today = date.today()

    if legacy_date and not range_preset and not date_from and not date_to:
        try:
            d = datetime.strptime(legacy_date, "%Y-%m-%d").date()
            return d, d, "day"
        except ValueError:
            pass

    preset = (range_preset or "today").strip().lower()
    if preset in RANGE_PRESETS:
        days_back = RANGE_PRESETS[preset]
        return today - timedelta(days=days_back), today, preset

    if preset == "custom" or date_from or date_to:
        start = today
        end = today
        if date_from:
            try:
                start = datetime.strptime(date_from, "%Y-%m-%d").date()
            except ValueError:
                pass
        if date_to:
            try:
                end = datetime.strptime(date_to, "%Y-%m-%d").date()
            except ValueError:
                pass
        if start > end:
            start, end = end, start
        return start, end, "custom"

    return today, today, "today"


def format_range_label(start: date, end: date, preset: str) -> str:
    if preset == "today":
        return "Today"
    if preset == "7d":
        return "Past 7 days"
    if preset == "30d":
        return "Past 30 days"
    if start == end:
        return start.strftime("%b %d, %Y")
    if start.year == end.year:
        return f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} – {end.strftime('%b %d, %Y')}"


def _in_range(ts: datetime, start: date, end: date) -> bool:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    return start_dt <= ts <= end_dt


def _build_recording_entry(
    file_path: str,
    file_name: str,
    cam_id: int,
    stat: os.stat_result,
    record_root: str,
) -> dict:
    size_mb = round(stat.st_size / (1024 * 1024), 2)
    start_time = datetime.fromtimestamp(stat.st_mtime)
    thumb_path, thumb_url = _thumb_paths_for_video(file_path, record_root)
    has_thumb = _ensure_thumbnail(file_path, thumb_path, stat.st_mtime)

    return {
        "filename": file_name,
        "path": _public_url_path_for_file(file_path),
        "size": f"{size_mb} MB",
        "size_bytes": stat.st_size,
        "camera": f"Camera {cam_id + 1}",
        "camera_id": cam_id,
        "timestamp": start_time.strftime("%H:%M:%S"),
        "full_timestamp": start_time,
        "date": start_time.strftime("%Y-%m-%d"),
        "thumbnail": thumb_url if has_thumb else None,
        "duration": "Unknown",
    }


def iter_recordings_in_range(
    start: date,
    end: date,
    camera_id: Optional[int] = None,
) -> Iterator[dict]:
    """Yield recording dicts whose mtime falls within [start, end] (inclusive)."""
    record_root = _abs_record_root()
    camera_ids = _discover_camera_ids(record_root)
    if camera_id is not None:
        camera_ids = [camera_id] if camera_id in camera_ids else []

    for cam_id in camera_ids:
        cam_dir = os.path.join(record_root, f"cam{cam_id + 1}")
        if not os.path.isdir(cam_dir):
            continue
        try:
            for root, _dirs, files in os.walk(cam_dir):
                for file_name in files:
                    if not file_name.endswith(VIDEO_EXTENSIONS):
                        continue
                    file_path = os.path.join(root, file_name)
                    try:
                        stat = os.stat(file_path)
                        start_time = datetime.fromtimestamp(stat.st_mtime)
                        if not _in_range(start_time, start, end):
                            continue
                        yield _build_recording_entry(
                            file_path, file_name, cam_id, stat, record_root,
                        )
                    except OSError as exc:
                        logger.warning("Error processing recording %s: %s", file_path, exc)
        except Exception as exc:
            logger.warning("Error scanning camera %s: %s", cam_id + 1, exc)


def get_recordings_for_range(
    start: date,
    end: date,
    camera_id: Optional[int] = None,
) -> List[dict]:
    recordings = list(iter_recordings_in_range(start, end, camera_id=camera_id))
    recordings.sort(key=lambda x: x["full_timestamp"], reverse=True)
    return recordings


def get_recordings_for_date(target_date: str) -> List[dict]:
    """Backward-compatible single-day query."""
    try:
        d = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        d = date.today()
    return get_recordings_for_range(d, d)


def get_camera_summaries_for_range(start: date, end: date) -> List[dict]:
    """One card per camera with recordings in range; thumb = latest clip."""
    by_camera: dict[int, dict] = {}

    for rec in iter_recordings_in_range(start, end):
        cam_id = rec["camera_id"]
        if cam_id not in by_camera:
            by_camera[cam_id] = {
                "camera_id": cam_id,
                "camera": rec["camera"],
                "clip_count": 0,
                "total_bytes": 0,
                "latest_timestamp": rec["full_timestamp"],
                "latest_path": rec["path"],
                "latest_filename": rec["filename"],
                "thumbnail": rec["thumbnail"],
                "latest_date": rec["date"],
                "latest_time": rec["timestamp"],
            }
        summary = by_camera[cam_id]
        summary["clip_count"] += 1
        summary["total_bytes"] += rec.get("size_bytes", 0)
        if rec["full_timestamp"] > summary["latest_timestamp"]:
            summary["latest_timestamp"] = rec["full_timestamp"]
            summary["latest_path"] = rec["path"]
            summary["latest_filename"] = rec["filename"]
            summary["thumbnail"] = rec["thumbnail"]
            summary["latest_date"] = rec["date"]
            summary["latest_time"] = rec["timestamp"]

    summaries = []
    for cam_id in sorted(by_camera.keys()):
        s = by_camera[cam_id]
        total_mb = round(s["total_bytes"] / (1024 * 1024), 1)
        s["total_size"] = f"{total_mb} MB" if total_mb >= 0.1 else "< 0.1 MB"
        summaries.append(s)

    return summaries


def refresh_thumbnail_for_camera(cam_id: int) -> bool:
    """Generate/update thumbnail for the newest recording file on a camera."""
    record_root = _abs_record_root()
    cam_dir = os.path.join(record_root, f"cam{cam_id + 1}")
    if not os.path.isdir(cam_dir):
        return False

    latest_path = None
    latest_mtime = 0.0
    for root, _dirs, files in os.walk(cam_dir):
        for name in files:
            if not name.endswith(VIDEO_EXTENSIONS):
                continue
            path = os.path.join(root, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= latest_mtime:
                latest_mtime = mtime
                latest_path = path

    if not latest_path:
        return False

    thumb_path, _thumb_url = _thumb_paths_for_video(latest_path, record_root)
    return _ensure_thumbnail(latest_path, thumb_path, latest_mtime)


def serialize_recordings(recordings: List[dict]) -> List[dict]:
    out = []
    for rec in recordings:
        item = dict(rec)
        if isinstance(item.get("full_timestamp"), datetime):
            item["full_timestamp"] = item["full_timestamp"].isoformat()
        out.append(item)
    return out


def serialize_camera_summaries(summaries: List[dict]) -> List[dict]:
    out = []
    for s in summaries:
        item = dict(s)
        if isinstance(item.get("latest_timestamp"), datetime):
            item["latest_timestamp"] = item["latest_timestamp"].isoformat()
        out.append(item)
    return out


# /recordings/<path> serving lives in basebuddy/core/api/static_files.py
# (single owner, with explicit video MIME types).


@recordings_bp.route("/api/recordings/list")
def api_list_recordings():
    """API: List recordings for a date or range."""
    from flask import request

    start, end, preset = parse_date_range(
        range_preset=request.args.get("range"),
        date_from=request.args.get("from"),
        date_to=request.args.get("to"),
        legacy_date=request.args.get("date"),
    )
    cam = request.args.get("cam")
    camera_id = int(cam) if cam not in (None, "") else None

    recordings = get_recordings_for_range(start, end, camera_id=camera_id)
    view = request.args.get("view", "clips")
    cameras = []
    if view == "cameras":
        cameras = get_camera_summaries_for_range(start, end)

    return jsonify({
        "ok": True,
        "view": view,
        "range": preset,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "recordings": serialize_recordings(recordings),
        "cameras": serialize_camera_summaries(cameras),
        "count": len(recordings),
    })
