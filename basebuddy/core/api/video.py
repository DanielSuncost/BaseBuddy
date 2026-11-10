"""
Video streaming API endpoints.

Provides MJPEG video streams with and without ML annotations, plus live audio.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Generator, Optional

from flask import Blueprint, Response, jsonify

video_api = Blueprint('video_api', __name__)
logger = logging.getLogger("basebuddy")


@video_api.route('/<int:cam_id>')
def video_stream(cam_id):
    """
    MJPEG video stream with ML annotations.
    
    Args:
        cam_id: Camera ID
        
    Returns:
        Multipart MJPEG stream
    """
    import basebuddy.modules.state as shared_state
    from basebuddy.modules.config import JPEG_QUALITY
    from basebuddy.core.services.video_streaming_service import mjpeg_generator
    
    return Response(
        mjpeg_generator(cam_id, shared_state.grabbers, JPEG_QUALITY),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@video_api.route('/raw/<int:cam_id>')
def video_raw_stream(cam_id):
    """
    Raw MJPEG video stream without ML annotations.
    
    Lower latency stream for camera wall.
    
    Args:
        cam_id: Camera ID
        
    Returns:
        Multipart MJPEG stream
    """
    import basebuddy.modules.state as shared_state
    from basebuddy.modules.config import JPEG_QUALITY
    from basebuddy.core.services.video_streaming_service import mjpeg_raw_generator
    
    return Response(
        mjpeg_raw_generator(cam_id, shared_state.grabbers, JPEG_QUALITY, target_fps=15),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def _camera_source_url(cam_id: int) -> Optional[str]:
    import basebuddy.modules.state as shared_state

    grabber = (shared_state.grabbers or {}).get(cam_id)
    url = getattr(grabber, "url", None) if grabber is not None else None
    if url:
        return str(url).strip() or None
    try:
        from basebuddy.routes.cameras import load_camera_config

        cam = next((c for c in load_camera_config() if c.get("id") == cam_id), None)
        url = (cam or {}).get("url")
        return str(url).strip() if url else None
    except Exception:
        return None


def _mp3_audio_generator(source_url: str) -> Generator[bytes, None, None]:
    """Remux live source audio to MP3 for HTMLAudioElement playback."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-i",
        source_url,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        "-f",
        "mp3",
        "pipe:1",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


@video_api.route('/<int:cam_id>/audio')
def video_audio_stream(cam_id):
    """Live MP3 audio from the camera source (RTSP/RTMP/HLS when present)."""
    if not shutil.which("ffmpeg"):
        return jsonify({"ok": False, "error": "ffmpeg not available"}), 503
    source_url = _camera_source_url(cam_id)
    if not source_url:
        return jsonify({"ok": False, "error": "camera not found"}), 404

    return Response(
        _mp3_audio_generator(source_url),
        mimetype="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


