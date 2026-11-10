"""
Frame capture loops for RTSP, HTTP MJPEG, and still-image polling sources,
plus frame transformation (rotation/flip) from camera profiles.
"""
import logging
import os
import subprocess
import time
from typing import Optional, Tuple

import cv2
import numpy as np
import requests

from basebuddy.modules.config import MULTIPROC_DETECTION, FFMPEG_HWACCEL, DISPLAY_TARGET_FPS

logger = logging.getLogger(__name__)


class CaptureMixin:
    """Capture-source handling for FrameGrabber."""

    @staticmethod
    def _rtsp_capture_options() -> str:
        parts = ["rtsp_transport;tcp", "loglevel;quiet"]
        if FFMPEG_HWACCEL:
            parts.append(f"hwaccel;{FFMPEG_HWACCEL}")
        return "|".join(parts)

    @staticmethod
    def _uses_ffmpeg_pipe(url: str) -> bool:
        """RTMP / HLS: OpenCV VideoCapture often hangs under many concurrent cams."""
        u = (url or "").lower()
        return u.startswith("rtmp://") or ".m3u8" in u

    def _stop_ffmpeg_pipe(self) -> None:
        proc = getattr(self, "_ffmpeg_proc", None)
        self._ffmpeg_proc = None
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _iter_ffmpeg_mjpeg(self):
        """Spawn ffmpeg and yield decoded BGR frames from an MJPEG pipe."""
        url = self.url
        is_hls = ".m3u8" in (url or "").lower()
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        # Live RTMP: prefer low latency. HLS: avoid nobuffer — it dumps whole
        # segments as bursts; -re paces demux to the segment timestamps.
        if is_hls:
            cmd.append("-re")
        else:
            cmd.extend(["-fflags", "nobuffer", "-flags", "low_delay"])
        if (url or "").lower().startswith("http"):
            cmd.extend([
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ])
        cmd.extend([
            "-i", url,
            "-an",
            "-f", "mjpeg",
            "-q:v", "5",
            "-",
        ])
        logger.info(f"Camera {self.cam_id}: Starting ffmpeg pipe for {url}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._ffmpeg_proc = proc
        buf = b""
        # Back-pressure pacing for HLS: sleeping between yields fills the pipe
        # and blocks ffmpeg, stretching each ~4s segment across realtime.
        pace_interval = (1.0 / max(DISPLAY_TARGET_FPS, 1)) if is_hls else 0.0
        last_paced = 0.0
        try:
            while self.running and self.camera_enabled and proc.poll() is None:
                chunk = proc.stdout.read(4096) if proc.stdout else b""
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 8_000_000:
                    buf = buf[-1_000_000:]
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start < 0:
                        buf = b""
                        break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        buf = buf[start:]
                        break
                    jpg = buf[start : end + 2]
                    buf = buf[end + 2 :]
                    frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    if pace_interval:
                        wait = pace_interval - (time.time() - last_paced)
                        if wait > 0:
                            time.sleep(wait)
                        last_paced = time.time()
                    yield frame
        finally:
            self._stop_ffmpeg_pipe()

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], Optional[float]]:
        """Get the latest frame and its timestamp, with transformations applied"""
        with self.lock:
            if self.frames:
                frame = self.frames[-1].copy()
                # Apply transformations from profile
                frame = self._apply_transformations(frame)
                return frame, self.timestamps[-1]
            return None, None

    # How long after a detection *result* to keep drawing boxes on the live wall.
    # Tied to result completion time (not capture time) so multi-cam GPU wait /
    # inference latency does not hide overlays. Keep long enough to cover
    # ~0.5 idle FPS scheduling across many shared-GPU cameras.
    WALL_OVERLAY_MAX_AGE = 10.0
    # Only reject for severe capture lag (true backlog). Normal inference latency
    # and CUDA contention routinely push live_ts - det_frame_ts above a few seconds
    # even when drain-to-newest is working — that must not suppress boxes.
    WALL_OVERLAY_FRAME_LAG_MAX = 20.0
    WALL_OVERLAY_TRAIL_MAX_AGE = 15.0
    WALL_TRAIL_MAX_POINTS = 40

    def get_latest_wall_frame(self) -> Tuple[Optional[np.ndarray], Optional[float]]:
        """Latest frame with lightweight detection overlays (boxes + track trails).

        Draws from cached detection results so the wall keeps the full stream
        frame rate even when detection itself runs much slower. Overlays are
        drawn before profile transformations because detection runs on raw
        (untransformed) frames.
        """
        with self.lock:
            if not self.frames:
                return None, None
            frame = self.frames[-1].copy()
            ts = self.timestamps[-1]
            detections = list(self.last_detections) if self.last_detections else []
            det_ts = getattr(self, 'last_detection_ts', 0) or 0
            det_frame_ts = getattr(self, 'last_detection_frame_ts', 0) or 0
            det_count = int(getattr(self, 'last_det_count', 0) or 0)
            trails = {
                tid: list(pts)
                for tid, pts in (getattr(self, 'wall_track_trails', {}) or {}).items()
            }

        now = time.time()
        trail_age = self.WALL_OVERLAY_TRAIL_MAX_AGE
        has_fresh_trails = any(
            len(pts) >= 2 and any(len(p) > 2 and now - p[2] < trail_age for p in pts)
            for pts in trails.values()
        )
        # Boxes drop when the latest pass found nothing; trails can linger briefly.
        show_boxes = det_count > 0 and bool(detections)
        if not show_boxes and not has_fresh_trails:
            return self._apply_transformations(frame), ts

        # result_age: freshness of the inference result (wall-clock completion).
        # frame_lag: only used to drop absurd backlog; never gated at inference budget.
        frame_lag = (ts - det_frame_ts) if det_frame_ts else 0.0
        result_age = time.time() - det_ts if det_ts else 999.0
        boxes_ok = (
            show_boxes
            and result_age < self.WALL_OVERLAY_MAX_AGE
            and frame_lag < self.WALL_OVERLAY_FRAME_LAG_MAX
        )
        if boxes_ok or has_fresh_trails:
            self._draw_wall_overlays(
                frame,
                detections if boxes_ok else [],
                trails,
            )
        return self._apply_transformations(frame), ts

    def _record_wall_trails(self, detections: list) -> None:
        """Append centroids for tracked detections into the wall trail cache."""
        if not detections:
            return
        now = time.time()
        trails = getattr(self, 'wall_track_trails', None)
        if trails is None:
            self.wall_track_trails = {}
            trails = self.wall_track_trails
        max_pts = self.WALL_TRAIL_MAX_POINTS
        max_age = self.WALL_OVERLAY_TRAIL_MAX_AGE
        seen = set()
        for det in detections:
            track_id = det.get('track_id')
            if track_id is None:
                continue
            bbox = det.get('bbox')
            if bbox is None or len(bbox) != 4:
                continue
            try:
                tid = int(track_id)
                x1, y1, x2, y2 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                continue
            cx, cy = int((x1 + x2) / 2), int((y1 + y2) / 2)
            pts = trails.setdefault(tid, [])
            pts.append((cx, cy, now))
            if len(pts) > max_pts:
                del pts[:-max_pts]
            seen.add(tid)
        # Drop stale tracks not updated this pass
        stale = [
            tid for tid, pts in trails.items()
            if tid not in seen and (not pts or now - pts[-1][2] > max_age)
        ]
        for tid in stale:
            trails.pop(tid, None)

    def _draw_wall_overlays(self, frame: np.ndarray, detections: list, trails: dict = None) -> None:
        """Draw bounding boxes and track trails from cached detection state."""
        try:
            detector = self.detector
            now = time.time()
            trail_age = self.WALL_OVERLAY_TRAIL_MAX_AGE

            # Prefer grabber-side trails (works with multiproc); fall back to detector history.
            trail_sources = []
            if trails:
                trail_sources.append(trails)
            elif getattr(self, 'wall_track_trails', None):
                trail_sources.append(self.wall_track_trails)
            if detector is not None and getattr(detector, 'track_history', None):
                trail_sources.append(detector.track_history)

            drawn_ids = set()
            for source in trail_sources:
                for track_id, points in list(source.items()):
                    if track_id in drawn_ids:
                        continue
                    pts = [p for p in list(points) if len(p) > 2 and now - p[2] < trail_age]
                    if len(pts) < 2:
                        continue
                    color = (0, 200, 255)
                    if detector is not None:
                        try:
                            color = detector.get_track_color(track_id)
                        except Exception:
                            pass
                    for i in range(1, len(pts)):
                        cv2.line(
                            frame,
                            (int(pts[i - 1][0]), int(pts[i - 1][1])),
                            (int(pts[i][0]), int(pts[i][1])),
                            color, 2,
                        )
                    drawn_ids.add(track_id)

            for det in detections:
                bbox = det.get('bbox')
                if bbox is None or len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = (int(v) for v in bbox)
                color = (0, 255, 128)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label = det.get('class_name') or ''
                track_id = det.get('track_id')
                if track_id is not None:
                    label = f"{label} #{track_id}".strip()
                if label:
                    cv2.putText(frame, label, (x1, max(14, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
        except Exception as e:
            # Overlay drawing must never break the wall stream
            logger.debug("Wall overlay draw failed for cam %s: %s", getattr(self, 'cam_id', '?'), e)

    def _apply_transformations(self, frame: np.ndarray) -> np.ndarray:
        """Apply rotation and flip transformations from camera profile"""
        if not hasattr(self, '_profile_cache') or self._profile_cache_time < time.time() - 5:
            # Cache profile for 5 seconds to avoid DB hits
            try:
                from basebuddy.modules.camera_profiles import get_profile_manager
                manager = get_profile_manager()
                self._profile_cache = manager.get_profile(self.cam_id)
                self._profile_cache_time = time.time()
            except Exception:
                self._profile_cache = None
                self._profile_cache_time = time.time()

        if not self._profile_cache:
            return frame

        profile = self._profile_cache

        # Apply rotation (0, 90, 180, 270 degrees)
        if profile.rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif profile.rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif profile.rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # Apply flips
        if profile.flip_horizontal and profile.flip_vertical:
            frame = cv2.flip(frame, -1)  # Both
        elif profile.flip_horizontal:
            frame = cv2.flip(frame, 1)  # Horizontal
        elif profile.flip_vertical:
            frame = cv2.flip(frame, 0)  # Vertical

        return frame

    def _read_mjpeg_stream(self):
        """Read frames from HTTP MJPEG stream"""
        try:
            # Open the stream with requests for better control
            stream = requests.get(self.url, stream=True, timeout=10)
            if stream.status_code != 200:
                logger.error(f"Camera {self.cam_id}: HTTP error {stream.status_code}")
                return None

            self.mjpeg_stream = stream
            bytes_data = b''

            while self.running and self.camera_enabled:
                try:
                    # Read data chunk from stream
                    chunk = stream.raw.read(1024)
                    if not chunk:
                        logger.warning(f"Camera {self.cam_id}: Stream ended")
                        return None

                    bytes_data += chunk

                    # Find JPEG boundaries
                    a = bytes_data.find(b'\xff\xd8')  # JPEG start
                    b = bytes_data.find(b'\xff\xd9')  # JPEG end

                    if a != -1 and b != -1:
                        jpg = bytes_data[a:b+2]
                        bytes_data = bytes_data[b+2:]

                        # Decode JPEG to frame
                        frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if frame is not None:
                            yield frame

                except Exception as e:
                    logger.warning(f"Camera {self.cam_id}: Error reading MJPEG chunk: {e}")
                    time.sleep(0.1)
                    continue

        except Exception as e:
            logger.error(f"Camera {self.cam_id}: Failed to open MJPEG stream: {e}")
            return None
        finally:
            if self.mjpeg_stream:
                try:
                    self.mjpeg_stream.close()
                except Exception:
                    pass
                self.mjpeg_stream = None

    def _grab_frames(self):
        """Main frame grabbing loop with continuous detection"""
        # For MJPEG streams, use a different approach
        if self.stream_type == "mjpeg":
            self._grab_frames_mjpeg()
            return

        # For still image polling, use polling approach
        if self.stream_type == "still":
            self._grab_frames_still()
            return

        # RTMP/HLS: prefer subprocess pipe — OpenCV CAP_FFMPEG can deadlock
        # when many cameras open captures concurrently in one process.
        if self._uses_ffmpeg_pipe(self.url):
            self._grab_frames_ffmpeg_pipe()
            return

        # RTSP/HLS stream handling (original code)
        # HLS delivers frames in whole-segment bursts (often 10+ seconds per
        # segment), so unpaced reads produce a burst of frames followed by a
        # long stall. Pacing reads to the source fps keeps the frame buffer
        # filling smoothly at a constant one-segment delay.
        is_hls = ".m3u8" in (self.url or "").lower()
        pace_interval = 0.0
        last_paced_read = 0.0

        while self.running:
            try:
                # Check if camera is enabled (skip frame grabbing if disabled)
                if not self.camera_enabled:
                    time.sleep(0.1)  # Sleep briefly to prevent busy-waiting
                    continue

                if not self.cap or not self.cap.isOpened():
                    # Set RTSP options for lower latency and suppress warnings
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self._rtsp_capture_options()
                    self.cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
                    if self.cap.isOpened():
                        # Set buffer size to reduce latency
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if not self.cap.isOpened():
                        logger.warning(f"Camera {self.cam_id}: FFmpeg backend failed, trying default backend")
                        self.cap = cv2.VideoCapture(self.url)
                        if not self.cap.isOpened():
                            logger.error(f"Camera {self.cam_id}: All capture methods failed for {self.url}")
                            time.sleep(1.0)
                            continue
                    else:
                        logger.info(f"Camera {self.cam_id}: Connected using FFmpeg backend")
                    if self.cap.isOpened():
                        self.measured_fps = 14.0
                        logger.info(f"Camera {self.cam_id}: Using fixed FPS: {self.measured_fps:.1f}")
                        pace_interval = 0.0
                        if is_hls:
                            src_fps = self.cap.get(cv2.CAP_PROP_FPS) or 0
                            if 0 < src_fps <= 60:
                                pace_interval = 1.0 / src_fps
                                self.measured_fps = src_fps
                                logger.info(
                                    f"Camera {self.cam_id}: HLS stream, pacing reads at {src_fps:.1f} fps"
                                )

                if pace_interval:
                    wait = pace_interval - (time.time() - last_paced_read)
                    if wait > 0:
                        time.sleep(wait)
                    last_paced_read = time.time()

                ret, frame = self.cap.read()
                if ret:
                    ts = time.time()
                    self._last_successful_read = ts  # Track last successful frame

                    # Power management: Check if we should process this frame
                    should_process = True
                    is_power_limited = False
                    try:
                        from basebuddy.modules.power_management import get_power_manager
                        pm = get_power_manager()
                        should_process = pm.should_process_frame(self.frame_count)
                        is_power_limited = not should_process
                        if should_process and (self.detector or (MULTIPROC_DETECTION and self.tx is not None)):
                            # Also check if detection should run based on AI_FPS
                            should_process = pm.should_run_detection(self.cam_id)
                            is_power_limited = not should_process
                    except Exception:
                        # Power management not available, process normally
                        pass

                    # Check if detector is actually available
                    detector_available = (self.detector or (MULTIPROC_DETECTION and self.tx is not None))
                    should_run_detection = should_process and detector_available and self._should_enqueue_detection(ts)

                    if should_run_detection:
                        # Optimized: Only copy if queue is full (worker will copy if needed)
                        # This reduces memory bandwidth when queue has space
                        if len(self.detection_queue) >= self.detection_queue.maxlen - 1:
                            # Queue nearly full, drop oldest to make room
                            try:
                                self.detection_queue.popleft()
                            except IndexError:
                                pass
                        self.detection_queue.append((frame, ts))  # Reference, worker copies if needed
                    elif not detector_available and not is_power_limited and self.frame_count % 30 == 0:
                        # Only print message if detector is actually unavailable AND we're not in power-saving mode
                        logger.info(f"Camera {self.cam_id}: Detector not available")

                    # Use annotated frame for recording if available, but keep raw frames for wall streaming
                    processed_frame = self.last_annotated_frame if self.last_annotated_frame is not None else frame

                    self._clip_buffer.push(processed_frame, ts)
                    if self.last_detections:
                        names = [d.get("class_name") for d in self.last_detections if d.get("class_name")]
                        if names:
                            self.note_detection_classes(names)

                    if self.recording and self._should_write_recording_frame(processed_frame):
                        self._write_frame_to_recording(processed_frame)

                    with self.lock:
                        # Push raw frames to the wall buffer to keep high FPS on the camera wall
                        self.frames.append(frame)
                        self.timestamps.append(ts)
                    self.frame_count += 1

                    # Timelapse still capture
                    self._maybe_capture_still(frame, ts)
                else:
                    # Frame read failed
                    self._consecutive_failures = getattr(self, '_consecutive_failures', 0) + 1

                    # Check for stale connection (no frames for 10+ seconds)
                    last_read = getattr(self, '_last_successful_read', 0)
                    if last_read > 0 and time.time() - last_read > 10:
                        logger.warning(f"Camera {self.cam_id}: No frames for 10+ seconds, forcing reconnect")
                        if self.cap:
                            self.cap.release()
                            self.cap = None
                        self._consecutive_failures = 0
                        self._last_successful_read = 0
                        time.sleep(1.0)
                        continue

                    # After 50 consecutive failures, force reconnect
                    if self._consecutive_failures >= 50:
                        logger.warning(f"Camera {self.cam_id}: {self._consecutive_failures} consecutive read failures, forcing reconnect")
                        if self.cap:
                            self.cap.release()
                            self.cap = None
                        self._consecutive_failures = 0
                        time.sleep(1.0)
                        continue

                    time.sleep(0.1)
            except Exception as e:
                logger.info(f"Frame grabber error for camera {self.cam_id}: {e}")
                if self.cap:
                    self.cap.release()
                    self.cap = None
                self._consecutive_failures = 0
                time.sleep(1.0)

    def _grab_frames_ffmpeg_pipe(self):
        """Frame grabbing via ffmpeg MJPEG stdout (RTMP / HLS)."""
        while self.running:
            try:
                if not self.camera_enabled:
                    time.sleep(0.1)
                    continue

                frame_iter = self._iter_ffmpeg_mjpeg()
                first = True
                is_hls = ".m3u8" in (self.url or "").lower()
                for frame in frame_iter:
                    if not self.running or not self.camera_enabled:
                        break
                    if first:
                        self.measured_fps = float(DISPLAY_TARGET_FPS) if is_hls else 14.0
                        logger.info(
                            f"Camera {self.cam_id}: Connected using ffmpeg pipe "
                            f"({frame.shape[1]}x{frame.shape[0]})"
                            + (f", HLS paced at {self.measured_fps:.0f} fps" if is_hls else "")
                        )
                        first = False

                    ts = time.time()
                    self._last_successful_read = ts

                    should_process = True
                    is_power_limited = False
                    try:
                        from basebuddy.modules.power_management import get_power_manager
                        pm = get_power_manager()
                        should_process = pm.should_process_frame(self.frame_count)
                        is_power_limited = not should_process
                        if should_process and (self.detector or (MULTIPROC_DETECTION and self.tx is not None)):
                            should_process = pm.should_run_detection(self.cam_id)
                            is_power_limited = not should_process
                    except Exception:
                        pass

                    detector_available = (self.detector or (MULTIPROC_DETECTION and self.tx is not None))
                    should_run_detection = should_process and detector_available and self._should_enqueue_detection(ts)

                    if should_run_detection:
                        if len(self.detection_queue) >= self.detection_queue.maxlen - 1:
                            try:
                                self.detection_queue.popleft()
                            except IndexError:
                                pass
                        self.detection_queue.append((frame, ts))
                    elif not detector_available and not is_power_limited and self.frame_count % 30 == 0:
                        logger.info(f"Camera {self.cam_id}: Detector not available")

                    processed_frame = self.last_annotated_frame if self.last_annotated_frame is not None else frame
                    self._clip_buffer.push(processed_frame, ts)
                    if self.last_detections:
                        names = [d.get("class_name") for d in self.last_detections if d.get("class_name")]
                        if names:
                            self.note_detection_classes(names)

                    if self.recording and self._should_write_recording_frame(processed_frame):
                        self._write_frame_to_recording(processed_frame)

                    with self.lock:
                        self.frames.append(frame)
                        self.timestamps.append(ts)
                    self.frame_count += 1
                    self._maybe_capture_still(frame, ts)

                if self.running and self.camera_enabled:
                    logger.warning(f"Camera {self.cam_id}: ffmpeg pipe ended, reconnecting...")
                    time.sleep(1.0)
            except Exception as e:
                logger.error(f"Camera {self.cam_id}: ffmpeg pipe error: {e}")
                self._stop_ffmpeg_pipe()
                time.sleep(2.0)

    def _grab_frames_mjpeg(self):
        """Frame grabbing loop for HTTP MJPEG streams"""
        while self.running:
            try:
                # Check if camera is enabled
                if not self.camera_enabled:
                    time.sleep(0.1)
                    continue

                # Connect to MJPEG stream
                logger.info(f"Camera {self.cam_id}: Connecting to MJPEG stream at {self.url}")
                stream_reader = self._read_mjpeg_stream()

                if stream_reader is None:
                    logger.error(f"Camera {self.cam_id}: Failed to connect to MJPEG stream")
                    time.sleep(2.0)
                    continue

                logger.info(f"Camera {self.cam_id}: Connected to MJPEG stream")
                self.measured_fps = 10.0  # Typical for ESP32

                # Read frames from MJPEG stream
                for frame in stream_reader:
                    if not self.running or not self.camera_enabled:
                        break

                    ts = time.time()
                    self._last_successful_read = ts

                    # Power management: Check if we should process this frame
                    should_process = True
                    is_power_limited = False
                    try:
                        from basebuddy.modules.power_management import get_power_manager
                        pm = get_power_manager()
                        should_process = pm.should_process_frame(self.frame_count)
                        is_power_limited = not should_process
                        if should_process and (self.detector or (MULTIPROC_DETECTION and self.tx is not None)):
                            should_process = pm.should_run_detection(self.cam_id)
                            is_power_limited = not should_process
                    except Exception:
                        pass

                    # Check if detector is available
                    detector_available = (self.detector or (MULTIPROC_DETECTION and self.tx is not None))
                    should_run_detection = should_process and detector_available and self._should_enqueue_detection(ts)

                    if should_run_detection:
                        if len(self.detection_queue) >= self.detection_queue.maxlen - 1:
                            try:
                                self.detection_queue.popleft()
                            except IndexError:
                                pass
                        self.detection_queue.append((frame, ts))
                    elif not detector_available and not is_power_limited and self.frame_count % 30 == 0:
                        logger.info(f"Camera {self.cam_id}: Detector not available")

                    # Use annotated frame for recording if available
                    processed_frame = self.last_annotated_frame if self.last_annotated_frame is not None else frame

                    should_record_frame = True
                    if self.recording:
                        if self.motion_based_recording:
                            should_record_frame = self.detect_motion(processed_frame)
                        if should_record_frame and self.frame_skip_rate > 1:
                            self.frame_skip_counter += 1
                            should_record_frame = (self.frame_skip_counter % self.frame_skip_rate == 0)

                    if self.recording and should_record_frame:
                        self._write_frame_to_recording(processed_frame)

                    with self.lock:
                        self.frames.append(frame)
                        self.timestamps.append(ts)
                    self.frame_count += 1

                    # Timelapse still capture
                    self._maybe_capture_still(frame, ts)

                # Stream ended, reconnect
                logger.warning(f"Camera {self.cam_id}: MJPEG stream ended, reconnecting...")
                time.sleep(1.0)

            except Exception as e:
                logger.error(f"Camera {self.cam_id}: MJPEG grabber error: {e}")
                if self.mjpeg_stream:
                    try:
                        self.mjpeg_stream.close()
                    except Exception:
                        pass
                    self.mjpeg_stream = None
                time.sleep(2.0)

    def _grab_frames_still(self):
        """Poll HTTP endpoint for still images"""
        logger.info(f"Camera {self.cam_id}: Starting still image polling from {self.url} (every {self.poll_rate}s)")
        poll_interval = self.poll_rate  # Use configured polling rate

        while self.running:
            try:
                # Fetch still image from HTTP endpoint
                response = requests.get(self.url, timeout=5)

                if response.status_code == 200:
                    # Decode JPEG image
                    img_array = np.frombuffer(response.content, dtype=np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

                    if frame is not None:
                        ts = time.time()

                        # Add to buffer
                        with self.lock:
                            self.frames.append(frame)
                            self.timestamps.append(ts)

                        # Update display frame
                        self._maybe_update_display_frame(frame, ts)

                        self.frame_count += 1

                        # Timelapse still capture
                        self._maybe_capture_still(frame, ts)
                    else:
                        logger.warning(f"Camera {self.cam_id}: Failed to decode image")
                else:
                    logger.warning(f"Camera {self.cam_id}: HTTP {response.status_code} from {self.url}")

                # Wait before next poll
                time.sleep(poll_interval)

            except requests.exceptions.RequestException as e:
                logger.error(f"Camera {self.cam_id}: Still image request error: {e}")
                time.sleep(2.0)  # Wait longer on errors
            except Exception as e:
                logger.error(f"Camera {self.cam_id}: Still image grabber error: {e}")
                time.sleep(2.0)
