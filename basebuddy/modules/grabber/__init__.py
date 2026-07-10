"""
Frame grabber and recording logic.

FrameGrabber is composed from concern-specific mixins:
- environment: FFmpeg/OpenCV log suppression + multiprocessing spawn context
- capture: RTSP/MJPEG/still capture loops and frame transformations
- recording: FFmpeg segment recording, motion gating, event clips
- detection_pipeline: in-thread / child-process detection and watchdog
- stills: timelapse still capture and display-frame encoding
"""
import collections
import logging
import os
import threading
from typing import Deque, Optional, Tuple  # noqa: F401 (re-exported API surface)

# environment must be imported before cv2-dependent modules so the
# OPENCV_FFMPEG_* variables take effect.
from . import environment  # noqa: F401
from .environment import mp, _spawn_context  # noqa: F401 (compat re-export)

import numpy as np

from basebuddy.core.paths import get_stills_root
from basebuddy.core.services.event_clip_buffer import EventClipBuffer
from basebuddy.modules.config import (
    BUFFER_MAX_FRAMES, DISPLAY_MAX_WIDTH, DISPLAY_TARGET_FPS,
    DETECTION_IDLE_FPS, DETECTION_ACTIVE_FPS, DETECTION_ACTIVE_SECS,
    RECORDING_MODE, EVENT_CLIP_PRE_S,
)

from .capture import CaptureMixin
from .detection_pipeline import DetectionPipelineMixin
from .recording import RecordingMixin
from .stills import StillsMixin

logger = logging.getLogger(__name__)

__all__ = ["FrameGrabber"]


class FrameGrabber(CaptureMixin, RecordingMixin, DetectionPipelineMixin, StillsMixin):
    """Handles camera capture and recording"""

    def __init__(self, cam_id: int, url: str, buffer_max: int = BUFFER_MAX_FRAMES, pose_queue=None, stream_type: str = "rtsp", poll_rate: float = 2.0):
        self.cam_id = cam_id
        self.url = url
        self.stream_type = stream_type  # 'rtsp', 'mjpeg', or 'still'
        self.poll_rate = poll_rate  # Polling interval for 'still' cameras (seconds)
        self.buffer_max = buffer_max
        self.frames: Deque[np.ndarray] = collections.deque(maxlen=buffer_max)
        self.timestamps: Deque[float] = collections.deque(maxlen=buffer_max)
        self.pose_queue = pose_queue
        self.lock = threading.Lock()
        self.cap = None
        self.mjpeg_stream = None  # For HTTP MJPEG streams
        self.recording = False
        self.current_recording_start = None
        self.thread = None
        self.running = False
        self.detector = None
        self.analytics_db = None
        self.frame_count = 0
        self.recording_process = None
        self.recording_segment_start = None
        self.frame_skip_counter = 0
        self.frame_skip_rate = 1
        self.recording_fps = 0
        self.frame_timestamps = []
        self.last_frame_time = 0
        self.measured_fps = 30
        self.compression_preset = "ultrafast"
        self.compression_crf = "28"

        # Timelapse/still capture
        self._last_still_capture = 0
        self._still_capture_enabled = False
        self._still_capture_interval = 60  # seconds
        self._stills_folder = os.path.join(get_stills_root(), f'camera_{cam_id}')
        self.motion_based_recording = False
        self.motion_threshold = 500
        self.motion_detected = False
        self.pre_motion_seconds = 5
        self.post_motion_seconds = 10
        self.last_motion_time = 0

        # Event clips + detection-triggered recording
        self.recording_mode = RECORDING_MODE  # continuous | motion | detection | off
        self.recording_trigger_classes = None  # None = all classes
        self._detection_record_until = 0.0
        self._clip_buffer = EventClipBuffer(cam_id, pre_seconds=float(EVENT_CLIP_PRE_S))
        self._last_detections_for_record = []

        # Detection threading / IPC
        self.detection_queue = collections.deque(maxlen=30)  # Increased buffer to prevent frame drops
        # Kept for health-monitor compatibility; annotated frames are exposed via
        # latest_annotated_frame / latest_detection_jpeg_bytes, not stored per-timestamp.
        self.detection_results = {}
        # Optimized: Direct reference to latest annotated frame (no timestamp lookup needed)
        self.latest_annotated_frame = None
        self.latest_annotated_ts = None
        self.detection_thread = None
        self.detection_running = False
        self.proc = None
        self.tx = None
        self.rx = None
        self.pending_frames = {}
        self.last_annotated_frame = None
        self.last_det_count = 0
        self.last_detection_ts = 0.0
        self.watchdog_thread = None
        self.last_detections = []  # recent detections for click hit-test

        # Display stream caching (decoupled from detection pipeline)
        self.display_max_width = DISPLAY_MAX_WIDTH
        self.display_target_interval = 1.0 / max(DISPLAY_TARGET_FPS, 1)
        self._last_display_sent_ts = 0.0
        self.latest_display_jpeg_bytes = None
        self.latest_display_jpeg_ts = None

        # Annotated/detection JPEG cache
        self.latest_detection_jpeg_bytes = None
        self.latest_detection_jpeg_ts = None

        # Detection cadence (adaptive idle/active scheduling)
        self.idle_detection_fps = max(DETECTION_IDLE_FPS, 0.0)
        self.active_detection_fps = max(DETECTION_ACTIVE_FPS, 0.0)
        self.active_detection_window = max(DETECTION_ACTIVE_SECS, 0.0)
        self._last_detection_enqueue_ts = 0.0
        self._last_detection_activity_ts = 0.0

        # Camera enabled flag (controls frame grabbing)
        self.camera_enabled: bool = True

    def start(self):
        """Start the frame grabbing thread"""
        if self.thread and self.thread.is_alive():
            return

        self.running = True
        self.thread = threading.Thread(target=self._grab_frames, daemon=True)
        self.thread.start()

        self.start_detection_thread()

    def stop(self):
        """Stop the frame grabbing thread and clean up all resources"""
        logger.info(f"Camera {self.cam_id}: Stopping and cleaning up...")
        self.running = False
        self.camera_enabled = False

        # Release capture immediately so a blocked cap.read() can unwind
        cap = self.cap
        self.cap = None
        if cap:
            try:
                cap.release()
            except Exception as e:
                logger.warning(f"Camera {self.cam_id}: Error releasing capture: {e}")

        # Stop recording first
        try:
            if self.recording:
                self.stop_recording()
        except Exception as e:
            logger.warning(f"Camera {self.cam_id}: Error stopping recording: {e}")

        # Close MJPEG stream
        if self.mjpeg_stream:
            try:
                self.mjpeg_stream.close()
            except Exception:
                pass
            self.mjpeg_stream = None

        # Stop frame grabbing thread
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

        # Stop detection thread
        self.stop_detection_thread()

        # Clear all frame buffers and queues
        self.clear_cached_frames()
        self.detection_queue.clear()

        # Clean up detector (releases YOLO model from memory ~500MB)
        if self.detector:
            try:
                # Release detector resources
                if hasattr(self.detector, 'release_models'):
                    self.detector.release_models()
                elif hasattr(self.detector, 'model'):
                    del self.detector.model
                del self.detector
            except Exception as e:
                logger.warning(f"Camera {self.cam_id}: Error cleaning detector: {e}")
            self.detector = None

        # Clear analytics reference
        self.analytics_db = None

        logger.info(f"Camera {self.cam_id}: Cleanup complete")

    def clear_cached_frames(self):
        """Clear cached frames and JPEG buffers"""
        with self.lock:
            self.frames.clear()
            self.timestamps.clear()
            self.pending_frames.clear()
            self.detection_results.clear()
            self.latest_display_jpeg_bytes = None
            self.latest_display_jpeg_ts = None
            self.latest_detection_jpeg_bytes = None
            self.latest_detection_jpeg_ts = None
            self.latest_annotated_frame = None
            self.latest_annotated_ts = None
            self.last_annotated_frame = None
            self.last_det_count = 0
