"""
Ring buffer + event clip export for detection-triggered MP4 clips.

Frames are stored JPEG-compressed (~20x smaller than raw BGR) so that the
pre-roll ring and any in-flight sessions stay small. Sessions are hard-capped
in length, count, and age so a track that never ends (or a stalled detection
pipeline that never calls finalize) cannot grow memory without bound.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FrameEntry = Tuple[float, bytes]  # (timestamp, jpeg bytes)

MAX_CLIP_SECONDS = 60.0
MAX_ACTIVE_SESSIONS = 4
MAX_SESSION_AGE_S = 120.0
JPEG_QUALITY = 80


class _Session:
    __slots__ = ("frames", "started_at")

    def __init__(self, frames: Deque[FrameEntry]):
        self.frames = frames
        self.started_at = time.time()


class EventClipBuffer:
    def __init__(self, camera_id: int, pre_seconds: float = 5.0, fps: float = 14.0):
        self.camera_id = camera_id
        self.fps = max(4.0, fps)
        self.pre_seconds = pre_seconds
        maxlen = max(30, int(pre_seconds * self.fps) + 10)
        self._ring: Deque[FrameEntry] = deque(maxlen=maxlen)
        self._active: Dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._session_maxlen = int(MAX_CLIP_SECONDS * self.fps)

    def push(self, frame: np.ndarray, ts: Optional[float] = None) -> None:
        ts = ts or time.time()
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        entry = (ts, jpg.tobytes())
        now = time.time()
        with self._lock:
            self._ring.append(entry)
            expired = [
                sid for sid, sess in self._active.items()
                if now - sess.started_at > MAX_SESSION_AGE_S
            ]
            for sid in expired:
                self._active.pop(sid)
                logger.warning("Cam %s: dropped expired event clip session %s", self.camera_id, sid)
            for sess in self._active.values():
                sess.frames.append(entry)

    def start_session(self, session_id: str) -> None:
        with self._lock:
            if len(self._active) >= MAX_ACTIVE_SESSIONS:
                oldest = min(self._active, key=lambda sid: self._active[sid].started_at)
                self._active.pop(oldest)
                logger.warning(
                    "Cam %s: dropped event clip session %s (too many active)",
                    self.camera_id, oldest,
                )
            self._active[session_id] = _Session(deque(self._ring, maxlen=self._session_maxlen))

    def drop_all_sessions(self) -> int:
        """Discard all in-flight sessions and pre-roll frames. Returns count dropped."""
        with self._lock:
            count = len(self._active)
            self._active.clear()
            self._ring.clear()
        return count

    def finalize_session(self, session_id: str, post_seconds: float = 5.0) -> Optional[str]:
        """Wait briefly for post-roll then write MP4. Returns relative path."""
        deadline = time.time() + post_seconds
        while time.time() < deadline:
            time.sleep(0.15)

        with self._lock:
            session = self._active.pop(session_id, None)
        if session is None or len(session.frames) < 2:
            return None
        return self._write_clip(list(session.frames), session_id)

    def _write_clip(self, frames: List[FrameEntry], session_id: str) -> Optional[str]:
        try:
            from basebuddy.modules.config import RECORD_ROOT
        except Exception:
            RECORD_ROOT = "recordings"

        day = datetime.now().strftime("%Y-%m-%d")
        out_dir = os.path.join(RECORD_ROOT, f"cam{self.camera_id + 1}", "events", day)
        os.makedirs(out_dir, exist_ok=True)
        safe_id = session_id.replace("/", "_")[:48]
        out_path = os.path.join(out_dir, f"event_{safe_id}.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-f", "mjpeg",
            "-framerate", str(int(self.fps)),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-movflags", "+faststart",
            out_path,
        ]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            for _, jpg in frames:
                proc.stdin.write(jpg)
            proc.stdin.close()
            proc.wait(timeout=120)
            if proc.returncode == 0 and os.path.isfile(out_path):
                logger.info("Event clip saved: %s (%d frames)", out_path, len(frames))
                return out_path
        except Exception as exc:
            logger.warning("Event clip export failed cam %s: %s", self.camera_id, exc)
        return None
