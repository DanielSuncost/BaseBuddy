"""Default per-service retention policy (local + optional remote days)."""

DEFAULT_RETENTION_POLICY = {
    "recordings": {"local_days": 7, "remote_days": 0},
    # Detection crops grow very fast on multi-cam installs; keep short by default.
    "detections": {"local_days": 5, "remote_days": 0},
    "stills": {"local_days": 14, "remote_days": 0},
    "timelapse": {"local_days": 90, "remote_days": 0},
    "video_thumbs": {"local_days": 7, "remote_days": 0},
    "recording_thumbs": {"local_days": 7, "remote_days": 0},
}

RETENTION_SERVICE_LABELS = {
    "recordings": "Recordings (MP4)",
    "detections": "Detection stills",
    "stills": "Timelapse source stills",
    "timelapse": "Timelapse output",
    "video_thumbs": "Video thumbnails",
    "recording_thumbs": "Recording thumbnails",
}
