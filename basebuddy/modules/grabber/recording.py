"""
Recording pipeline: FFmpeg segment recording, motion detection gating,
event clips, and compression settings.
"""
import logging
import os
import subprocess
import time
from datetime import datetime
from typing import List, Optional

import cv2
import numpy as np

from basebuddy.modules.config import (
    RECORD_ROOT, SEG_MINUTES, JPEG_QUALITY, RECORDING_MODE,
)

logger = logging.getLogger(__name__)


class RecordingMixin:
    """Recording behavior for FrameGrabber."""

    def configure_recording(self, enabled: bool = True, mode: str = None, trigger_classes=None, motion_enabled: bool = False):
        """Apply recording policy from camera profile or env."""
        if not enabled or (mode or self.recording_mode) == "off":
            self.recording_mode = "off"
            if self.recording:
                self.stop_recording()
            return
        self.recording_mode = (mode or RECORDING_MODE or "continuous").lower()
        self.recording_trigger_classes = trigger_classes
        self.motion_based_recording = motion_enabled or self.recording_mode == "motion"
        if self.recording_mode in ("continuous", "motion", "detection") and not self.recording:
            self.start_recording()

    def start_event_clip(self, session_id: str) -> None:
        self._clip_buffer.start_session(session_id)

    def finalize_event_clip(self, session_id: str, post_seconds: float = 5.0) -> Optional[str]:
        return self._clip_buffer.finalize_session(session_id, post_seconds=post_seconds)

    def note_detection_classes(self, class_names: List[str]) -> None:
        """Extend detection-triggered recording window."""
        if self.recording_mode != "detection":
            return
        triggers = self.recording_trigger_classes
        if triggers:
            hit = any(c in triggers for c in class_names)
        else:
            hit = bool(class_names)
        if hit:
            from basebuddy.modules.config import EVENT_CLIP_POST_S
            self._detection_record_until = time.time() + float(EVENT_CLIP_POST_S) + 2.0

    def _should_write_recording_frame(self, processed_frame) -> bool:
        if not self.recording:
            return False
        mode = self.recording_mode
        if mode == "continuous":
            pass
        elif mode == "motion" or self.motion_based_recording:
            if not self.detect_motion(processed_frame):
                return False
        elif mode == "detection":
            active = time.time() < self._detection_record_until
            if not active:
                names = [d.get("class_name") for d in (self.last_detections or [])]
                self.note_detection_classes(names)
                active = time.time() < self._detection_record_until
            if not active:
                return False
        elif mode == "off":
            return False
        if self.frame_skip_rate > 1:
            self.frame_skip_counter += 1
            return self.frame_skip_counter % self.frame_skip_rate == 0
        return True

    def is_recording(self) -> bool:
        return self.recording

    def get_recording_duration(self) -> float:
        if self.recording and self.current_recording_start:
            return time.time() - self.current_recording_start
        return 0

    def get_recording_fps(self) -> float:
        if self.frame_skip_rate <= 0:
            return 0
        return 14 / self.frame_skip_rate

    def set_motion_based_recording(self, enabled: bool, threshold: int = 500, pre_seconds: int = 5, post_seconds: int = 10):
        self.motion_based_recording = enabled
        self.motion_threshold = threshold
        self.pre_motion_seconds = pre_seconds
        self.post_motion_seconds = post_seconds
        logger.info(f"Camera {self.cam_id}: Motion-based recording {'enabled' if enabled else 'disabled'}")

    def detect_motion(self, frame) -> bool:
        if not self.motion_based_recording:
            return True
        if not hasattr(self, 'prev_frame'):
            self.prev_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            return False
        current_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame_diff = cv2.absdiff(current_gray, self.prev_frame)
        _, thresh = cv2.threshold(frame_diff, 25, 255, cv2.THRESH_BINARY)
        motion_score = cv2.countNonZero(thresh)
        self.prev_frame = current_gray
        motion_detected = motion_score > self.motion_threshold
        if motion_detected:
            self.last_motion_time = time.time()
            if not self.motion_detected:
                logger.info(f"Camera {self.cam_id}: Motion detected (score: {motion_score})")
                self.motion_detected = True
        elif self.motion_detected:
            if time.time() - self.last_motion_time > self.post_motion_seconds:
                logger.info(f"Camera {self.cam_id}: Motion ended")
                self.motion_detected = False
        should_record = False
        current_time = time.time()
        if self.motion_detected:
            should_record = True
        elif current_time - self.last_motion_time < self.pre_motion_seconds:
            should_record = True
        return should_record

    def start_recording(self) -> bool:
        try:
            if self.recording:
                return True
            self.recording = True
            self.current_recording_start = time.time()
            self.recording_segment_start = time.time()
            self._start_ffmpeg_recording()
            logger.info(f"Camera {self.cam_id}: Recording started")
            return True
        except Exception as e:
            logger.error(f"Camera {self.cam_id}: Failed to start recording: {e}")
            self.recording = False
            self.current_recording_start = None
            return False

    def stop_recording(self) -> bool:
        try:
            if not self.recording:
                return True
            self.recording = False
            if self.recording_process:
                try:
                    self.recording_process.stdin.close()
                    self.recording_process.wait(timeout=5.0)
                except Exception:
                    # ffmpeg didn't exit cleanly — don't leak the process
                    try:
                        self.recording_process.kill()
                        self.recording_process.wait(timeout=2.0)
                    except Exception:
                        pass
                self.recording_process = None
            self._refresh_latest_recording_thumbnail()
            logger.info(f"Camera {self.cam_id}: Recording stopped")
            self.current_recording_start = None
            self.recording_segment_start = None
            return True
        except Exception as e:
            logger.error(f"Camera {self.cam_id}: Failed to stop recording: {e}")
            return False

    def set_frame_skip_rate(self, rate: int):
        self.frame_skip_rate = max(1, rate)
        logger.info(f"Camera {self.cam_id}: Frame skip rate set to {self.frame_skip_rate}")

    def set_compression_quality(self, quality: str):
        quality_settings = {
            "high": {"preset": "ultrafast", "crf": "28"},
            "medium": {"preset": "fast", "crf": "26"},
            "low": {"preset": "slow", "crf": "24"},
            "ultra_low": {"preset": "veryslow", "crf": "22"}
        }
        if quality in quality_settings:
            self.compression_preset = quality_settings[quality]["preset"]
            self.compression_crf = quality_settings[quality]["crf"]
            logger.info(f"Camera {self.cam_id}: Compression set to {quality} quality")
        else:
            logger.error(f"Invalid quality setting: {quality}")

    def measure_camera_fps(self, duration: int = 5) -> float:
        self.measured_fps = 14.0
        logger.info(f"Camera {self.cam_id}: Using fixed FPS: {self.measured_fps:.1f}")
        return 14.0

    def _start_ffmpeg_recording(self):
        try:
            args = self._ffmpeg_args("cont")
            logger.info(f"Camera {self.cam_id}: Starting FFmpeg with args: {' '.join(args)}")
            self.recording_process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.error(f"Camera {self.cam_id}: Failed to start FFmpeg: {e}")
            self.recording = False

    def _write_frame_to_recording(self, frame: np.ndarray):
        if not self.recording or not self.recording_process or self.recording_process.poll() is not None:
            return
        try:
            segment_duration = SEG_MINUTES * 60
            if time.time() - self.recording_segment_start >= segment_duration:
                if self.recording_process:
                    try:
                        self.recording_process.stdin.close()
                        self.recording_process.wait(timeout=2.0)
                    except Exception:
                        try:
                            self.recording_process.kill()
                            self.recording_process.wait(timeout=1.0)
                        except Exception:
                            pass
                self._refresh_latest_recording_thumbnail()
                self._start_ffmpeg_recording()
                self.recording_segment_start = time.time()
            ok, encoded_frame = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                self.recording_process.stdin.write(encoded_frame.tobytes())
        except Exception as e:
            logger.error(f"Camera {self.cam_id}: Error writing frame to recording: {e}")
            if self.recording:
                # Kill the old process before replacing it so it isn't leaked
                if self.recording_process:
                    try:
                        self.recording_process.kill()
                        self.recording_process.wait(timeout=1.0)
                    except Exception:
                        pass
                self._start_ffmpeg_recording()
                self.recording_segment_start = time.time()

    def _refresh_latest_recording_thumbnail(self) -> None:
        """Generate a preview JPEG for the most recently written segment."""
        try:
            from basebuddy.pages.recordings.api import refresh_thumbnail_for_camera
            refresh_thumbnail_for_camera(self.cam_id)
        except Exception as exc:
            logger.debug("Camera %s: thumbnail refresh skipped: %s", self.cam_id, exc)

    def _ffmpeg_args(self, kind: str = "cont") -> List[str]:
        outdir = os.path.join(RECORD_ROOT, f"cam{self.cam_id+1}",
                              datetime.now().strftime("%Y-%m-%d"),
                              datetime.now().strftime("%H"))
        os.makedirs(outdir, exist_ok=True)
        base = os.path.join(outdir, f"cam{self.cam_id+1}_{kind}_%Y-%m-%d_%H-%M-%S")
        actual_fps = 14
        return [
            "ffmpeg",
            "-f", "mjpeg",
            "-framerate", str(actual_fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", self.compression_preset,
            "-crf", self.compression_crf,
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            "-f", "segment",
            "-segment_time", str(SEG_MINUTES * 60),
            "-strftime", "1",
            base + ".mp4"
        ]
