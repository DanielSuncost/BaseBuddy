"""
Frame capture loops for RTSP, HTTP MJPEG, and still-image polling sources,
plus frame transformation (rotation/flip) from camera profiles.
"""
import logging
import os
import time
from typing import Optional, Tuple

import cv2
import numpy as np
import requests

from basebuddy.modules.config import MULTIPROC_DETECTION, FFMPEG_HWACCEL

logger = logging.getLogger(__name__)


class CaptureMixin:
    """Capture-source handling for FrameGrabber."""

    @staticmethod
    def _rtsp_capture_options() -> str:
        parts = ["rtsp_transport;tcp", "loglevel;quiet"]
        if FFMPEG_HWACCEL:
            parts.append(f"hwaccel;{FFMPEG_HWACCEL}")
        return "|".join(parts)

    def get_latest_frame(self) -> Tuple[Optional[np.ndarray], Optional[float]]:
        """Get the latest frame and its timestamp, with transformations applied"""
        with self.lock:
            if self.frames:
                frame = self.frames[-1].copy()
                # Apply transformations from profile
                frame = self._apply_transformations(frame)
                return frame, self.timestamps[-1]
            return None, None

    # Only draw cached detections if they are at most this old; avoids showing
    # stale boxes long after the objects have left the frame.
    WALL_OVERLAY_MAX_AGE = 10.0

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

        if detections and (time.time() - det_ts) < self.WALL_OVERLAY_MAX_AGE:
            self._draw_wall_overlays(frame, detections)
        return self._apply_transformations(frame), ts

    def _draw_wall_overlays(self, frame: np.ndarray, detections: list) -> None:
        """Draw bounding boxes and track trails from cached detection state."""
        try:
            detector = self.detector
            # Track trails first so boxes render on top
            if detector is not None and getattr(detector, 'track_history', None):
                now = time.time()
                for track_id, points in list(detector.track_history.items()):
                    pts = [p for p in list(points) if len(p) > 2 and now - p[2] < 30.0]
                    if len(pts) < 2:
                        continue
                    color = (0, 200, 255)
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
        except Exception:
            # Overlay drawing must never break the wall stream
            pass

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
