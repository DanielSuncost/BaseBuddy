"""
Timelapse still capture and downscaled display-frame encoding.
"""
import logging
import os
import time
from datetime import datetime

import cv2
import numpy as np

from basebuddy.modules.config import JPEG_QUALITY

logger = logging.getLogger(__name__)


class StillsMixin:
    """Still-image and display-frame handling for FrameGrabber."""

    def _maybe_capture_still(self, frame: np.ndarray, timestamp: float):
        """Capture still image for timelapse if enabled and interval elapsed"""
        # Check if still capture is enabled (refresh from profile periodically)
        if self.frame_count % 100 == 0:  # Check every 100 frames
            try:
                from basebuddy.modules.camera_profiles import get_profile_manager
                profile = get_profile_manager().get_profile(self.cam_id)
                self._still_capture_enabled = profile.still_capture_enabled
                self._still_capture_interval = profile.still_capture_interval_seconds or 60
                self._still_capture_start_hour = getattr(profile, 'still_capture_start_hour', 6)
                self._still_capture_end_hour = getattr(profile, 'still_capture_end_hour', 20)
                self._still_capture_skip_dark = getattr(profile, 'still_capture_skip_dark', True)
                self._still_capture_min_brightness = getattr(profile, 'still_capture_min_brightness', 15)
                if profile.still_capture_folder:
                    self._stills_folder = profile.still_capture_folder
            except Exception:
                pass

        if not self._still_capture_enabled:
            return

        # Check if enough time has passed since last capture
        if timestamp - self._last_still_capture < self._still_capture_interval:
            return

        # Check if within capture time window
        dt = datetime.fromtimestamp(timestamp)
        current_hour = dt.hour
        start_hour = getattr(self, '_still_capture_start_hour', 6)
        end_hour = getattr(self, '_still_capture_end_hour', 20)

        # Handle time ranges (supports overnight ranges like 22-6)
        if start_hour <= end_hour:
            # Normal range (e.g., 6-20)
            if not (start_hour <= current_hour < end_hour):
                return
        else:
            # Overnight range (e.g., 22-6)
            if not (current_hour >= start_hour or current_hour < end_hour):
                return

        # Check frame brightness - skip if too dark (nearly black frames)
        skip_dark = getattr(self, '_still_capture_skip_dark', True)
        min_brightness = getattr(self, '_still_capture_min_brightness', 15)
        if skip_dark:
            try:
                # Calculate mean brightness (convert to grayscale if needed)
                if len(frame.shape) == 3:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                else:
                    gray = frame
                mean_brightness = gray.mean()

                if mean_brightness < min_brightness:
                    # Frame is too dark, skip saving
                    if self.frame_count % 500 == 0:  # Log occasionally
                        logger.info(f"Camera {self.cam_id}: Skipping dark frame (brightness={mean_brightness:.1f} < {min_brightness})")
                    return
            except Exception:
                # If brightness check fails, proceed with saving anyway
                pass

        try:
            # Create folder if needed
            os.makedirs(self._stills_folder, exist_ok=True)

            # Apply transformations from profile
            save_frame = self._apply_transformations(frame.copy())

            # Generate filename with timestamp
            filename = dt.strftime('%Y%m%d_%H%M%S') + '.jpg'
            filepath = os.path.join(self._stills_folder, filename)

            # Save image
            cv2.imwrite(filepath, save_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            self._last_still_capture = timestamp

            # Log occasionally
            if self.frame_count % 1000 == 0:
                logger.info(f"Camera {self.cam_id}: Saved timelapse still to {filename}")
        except Exception as e:
            logger.warning(f"Camera {self.cam_id}: Error saving still: {e}")

    def _maybe_update_display_frame(self, frame: np.ndarray, timestamp: float):
        """Downscale/resample frame for display pipeline at a fixed cadence."""
        try:
            now = time.time()
            if (now - self._last_display_sent_ts) < self.display_target_interval:
                return

            frame_to_encode = frame
            if self.display_max_width and frame.shape[1] > self.display_max_width:
                scale = self.display_max_width / float(frame.shape[1])
                new_w = int(frame.shape[1] * scale)
                new_h = int(frame.shape[0] * scale)
                if new_w > 0 and new_h > 0:
                    frame_to_encode = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

            encode_params = [
                int(cv2.IMWRITE_JPEG_QUALITY), max(60, JPEG_QUALITY - 15),
                cv2.IMWRITE_JPEG_OPTIMIZE, 1,
            ]
            ok, jpg = cv2.imencode(".jpg", frame_to_encode, encode_params)
            if ok:
                with self.lock:
                    self.latest_display_jpeg_bytes = jpg.tobytes()
                    self.latest_display_jpeg_ts = timestamp
                self._last_display_sent_ts = now
        except Exception:
            pass  # Display encode failures shouldn't break grabbing
