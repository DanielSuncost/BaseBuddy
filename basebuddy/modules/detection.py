"""
Detection and tracking logic (YOLO + Supervision).
"""
import logging

logger = logging.getLogger(__name__)

import time
from datetime import datetime
from typing import Dict, Tuple, List, Optional
from collections import defaultdict

import cv2
import numpy as np


class DetectionTracker:
    """Handles YOLO detection and object tracking with adaptive display-only night mode"""

    def __init__(self, cam_id: int):
        from .config import (
            YOLO_AVAILABLE, TRACKER_TYPE
        )
        self.cam_id = cam_id
        self.tracker = None
        self.track_history = defaultdict(list)
        self.detection_enabled = YOLO_AVAILABLE
        self._inference_provider = None

        self.current_model_name = None
        self.is_dark_mode = False
        self.brightness_history = []
        self.brightness_window = 30

        self.day_threshold = 0.4
        self.night_threshold = 0.25

        self.frame_buffer = []
        self.accumulation_enabled = False

        self.performance_stats = {
            'total_frames': 0,
            'detection_time': [],
            'model_switches': 0,
            'last_model_switch': None,
            'current_fps': 0,
            'avg_detection_time': 0
        }

        self.max_track_age = 30
        self.max_track_history = 50
        self.track_cleanup_interval = 10
        self.frame_count = 0

        self.track_line_thickness = 2
        self.track_line_length = 20
        self.track_colors = {}
        self._apply_tracking_overrides()

        self.last_detections = []
        self._store_region_labels = []
        
        # Person Classifier & Pose
        self.classifier = None
        self.classifier_interval = 2.0  # Seconds between classifications for a track
        self.track_classification_history = defaultdict(float)
        self.track_names = {}
        self.track_classes = {}  # track_id -> detected class name (for traffic analytics)
        
        self.pose_queue = None
        self.track_pose_history = defaultdict(float)

        self._ensure_model_loaded()
        self.class_thresholds = self.load_class_thresholds()
        from .config import DISABLED_CLASSES
        self.disabled_classes = set(DISABLED_CLASSES)
        from .config import IGNORED_DETECTIONS
        self.ignored_detections = IGNORED_DETECTIONS.get(f"camera_{cam_id}", [])

        try:
            if self.detection_enabled:
                self._ensure_model_loaded()
                logger.info(f"Initialized adaptive detection for camera {cam_id} with {TRACKER_TYPE}")
                logger.info(f"Custom thresholds for camera {cam_id}: {self.class_thresholds}")
            else:
                logger.warning(f"Detection not available for camera {cam_id}")
        except Exception as e:
            logger.error(f"Failed to initialize detection for camera {cam_id}: {e}")
            self.detection_enabled = False

    @property
    def _provider(self):
        if self._inference_provider is None:
            from basebuddy.core.inference import get_inference_router
            self._inference_provider = get_inference_router().get_local_provider(self.cam_id)
        return self._inference_provider

    @property
    def model(self):
        return self._provider.active_model

    @property
    def day_model(self):
        return self._provider.day_model

    @property
    def night_model(self):
        return self._provider.night_model

    def release_models(self):
        if self._inference_provider is not None:
            self._inference_provider.release()
            self._inference_provider = None
            from basebuddy.core.inference import get_inference_router
            get_inference_router().release_camera(self.cam_id)

    def _ensure_model_loaded(self):
        try:
            import supervision as sv
            self._provider  # lazy-load YOLO via inference provider
            self.load_adaptive_thresholds()
            self.current_model_name = "day"
            from .config import TRACKER_TYPE
            if TRACKER_TYPE == "bytetrack":
                self.tracker = sv.ByteTrack()
            else:
                self.tracker = sv.ByteTrack()
        except Exception as e:
            logger.info(f"Model load error: {e}")
            
    def set_classifier(self, classifier):
        self.classifier = classifier
        logger.info(f"Person classifier attached to Camera {self.cam_id}")

    def set_pose_queue(self, queue):
        self.pose_queue = queue
        logger.info(f"Pose queue attached to Camera {self.cam_id}")

    def reload_disabled_classes(self):
        from .config import reload_disabled_classes
        self.disabled_classes = set(reload_disabled_classes())
        logger.info(f"Reloaded disabled classes for camera {self.cam_id}: {self.disabled_classes}")

    def reload_ignored_detections(self):
        from .config import reload_ignored_detections
        self.ignored_detections = reload_ignored_detections().get(f"camera_{self.cam_id}", [])
        logger.info(f"Reloaded ignored detections for camera {self.cam_id}: {len(self.ignored_detections)} detections")

    def is_detection_ignored(self, bbox: np.ndarray, class_name: str) -> bool:
        x1, y1, x2, y2 = bbox
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        width = x2 - x1
        height = y2 - y1
        for ignored in self.ignored_detections:
            if ignored.get('class_name') != class_name:
                continue
            ignored_center_x = ignored.get('center_x', 0)
            ignored_center_y = ignored.get('center_y', 0)
            ignored_width = ignored.get('width', 0)
            ignored_height = ignored.get('height', 0)
            center_distance = ((center_x - ignored_center_x) ** 2 + (center_y - ignored_center_y) ** 2) ** 0.5
            size_ratio = min(width, height) / max(ignored_width, ignored_height) if ignored_width > 0 and ignored_height > 0 else 1.0
            if center_distance < 50 and 0.5 <= size_ratio <= 2.0:
                return True
        return False

    def is_instance_ignored(self, bbox: np.ndarray, class_name: str, frame: np.ndarray) -> bool:
        """Check if a detection matches a previously ignored instance via color histogram + IoU.
        Uses 16-bin per-channel histograms and correlation metric.
        """
        try:
            from .config import IGNORED_INSTANCES
            camera_key = f"camera_{self.cam_id}"
            entries = IGNORED_INSTANCES.get(camera_key, [])
            if not entries:
                return False
            x1, y1, x2, y2 = map(int, bbox)
            h, w = frame.shape[:2]
            x1 = max(0, min(w-1, x1)); x2 = max(0, min(w-1, x2))
            y1 = max(0, min(h-1, y1)); y2 = max(0, min(h-1, y2))
            if x2 <= x1 or y2 <= y1:
                return False
            import cv2, numpy as np
            crop = frame[y1:y2, x1:x2]
            chans = cv2.split(crop)
            hist_bins = 16
            hists = [cv2.calcHist([ch], [0], None, [hist_bins], [0,256]).flatten() for ch in chans]
            q = np.concatenate(hists).astype(float)
            s = q.sum();
            if s > 0:
                q /= s
            # helper IoU
            def iou(a, b):
                ax1, ay1, ax2, ay2 = a
                bx1, by1, bx2, by2 = b
                ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                ix2, iy2 = min(ax2, bx2), min(ay2, by2)
                iw, ih = max(0, ix2-ix1), max(0, iy2-iy1)
                inter = iw*ih
                ua = max(0, ax2-ax1) * max(0, ay2-ay1) + max(0, bx2-bx1) * max(0, by2-by1) - inter
                return inter/ua if ua > 0 else 0.0
            cur_bbox = [float(x1), float(y1), float(x2), float(y2)]
            for e in entries:
                if e.get('class_name') != class_name:
                    continue
                ref_bbox = e.get('bbox')
                if not ref_bbox:
                    continue
                if iou(cur_bbox, ref_bbox) < 0.3:
                    continue
                ref_hist = e.get('hist')
                if not ref_hist:
                    # If no hist stored, IoU alone not sufficient → don't ignore
                    continue
                ref = np.array(ref_hist, dtype=float)
                # correlation: (q·r) / (||q|| ||r||)
                denom = (np.linalg.norm(q) * np.linalg.norm(ref))
                corr = float(q.dot(ref) / denom) if denom > 0 else 0.0
                if corr > 0.90:
                    return True
        except Exception:
            return False
        return False

    def is_filtered_by_roi(self, bbox: np.ndarray, frame_shape: Tuple[int, int, int]) -> bool:
        """Return True if detection should be dropped based on region filter rules."""
        try:
            from basebuddy.core.regions import load_camera_regions, should_filter_detection
            regions = load_camera_regions(self.cam_id)
            return should_filter_detection(bbox, frame_shape, regions)
        except Exception:
            return False

    def process_region_metadata(
        self,
        bbox: np.ndarray,
        class_name: str,
        frame_shape: Tuple[int, int, int],
        confidence: float = 0.0,
        track_id: Optional[int] = None,
    ) -> List[str]:
        """Tag labels and fire notifications for a kept detection."""
        try:
            from basebuddy.core.regions import load_camera_regions, notify_regions_for_detection, tag_labels_for_bbox
            from basebuddy.core.services.region_notifications import get_region_notification_service

            regions = load_camera_regions(self.cam_id)
            labels = tag_labels_for_bbox(bbox, frame_shape, regions)
            svc = get_region_notification_service()
            for region in notify_regions_for_detection(bbox, class_name, frame_shape, regions):
                svc.maybe_notify(
                    self.cam_id, region, class_name,
                    confidence=confidence, track_id=track_id,
                )
            return labels
        except Exception:
            return []

    def is_inside_ignored_roi(self, bbox: np.ndarray, frame_shape: Tuple[int, int, int]) -> bool:
        """Legacy alias — exclude-only check."""
        return self.is_filtered_by_roi(bbox, frame_shape)

    def load_adaptive_thresholds(self):
        try:
            from .config import DAY_CONF, NIGHT_CONF, ADAPTIVE_MODE, AI_CONF
            if ADAPTIVE_MODE:
                self.day_threshold = DAY_CONF
                self.night_threshold = NIGHT_CONF
                logger.info(f"Adaptive thresholds loaded: Day={DAY_CONF}, Night={NIGHT_CONF}")
            else:
                self.day_threshold = AI_CONF
                self.night_threshold = AI_CONF
        except Exception as e:
            logger.info(f"Error loading adaptive thresholds: {e}")

    def enhance_night_frame(self, frame: np.ndarray) -> np.ndarray:
        try:
            from .config import NIGHT_ENHANCEMENT, NIGHT_GAMMA
            if not NIGHT_ENHANCEMENT:
                return frame
            # Convention: gamma < 1.0 brightens, gamma > 1.0 darkens
            gamma = max(0.1, float(NIGHT_GAMMA))
            table = (np.array([((i / 255.0) ** gamma) * 255 for i in range(256)])
                     .clip(0, 255).astype("uint8"))
            return cv2.LUT(frame, table)
        except Exception as e:
            logger.info(f"Error in gamma adjustment: {e}")
            return frame

    def calculate_brightness(self, frame: np.ndarray) -> float:
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            brightness = np.mean(gray) / 255.0
            self.brightness_history.append(brightness)
            if len(self.brightness_history) > self.brightness_window:
                self.brightness_history.pop(0)
            return np.mean(self.brightness_history)
        except Exception as e:
            logger.info(f"Error calculating brightness: {e}")
            return 0.5

    def update_performance_stats(self, detection_time: float):
        try:
            self.performance_stats['total_frames'] += 1
            self.performance_stats['detection_time'].append(detection_time)
            if len(self.performance_stats['detection_time']) > 100:
                self.performance_stats['detection_time'].pop(0)
            if self.performance_stats['detection_time']:
                self.performance_stats['avg_detection_time'] = sum(self.performance_stats['detection_time']) / len(self.performance_stats['detection_time'])
                self.performance_stats['current_fps'] = 1.0 / self.performance_stats['avg_detection_time'] if self.performance_stats['avg_detection_time'] > 0 else 0
            if self.performance_stats['total_frames'] % 100 == 0:
                self.log_performance_stats()
        except Exception as e:
            logger.info(f"Error updating performance stats: {e}")

    def log_performance_stats(self):
        try:
            stats = self.performance_stats
            logger.info(f"Camera {self.cam_id} Performance: FPS={stats['current_fps']:.1f}, Avg Detection Time={stats['avg_detection_time']*1000:.1f}ms, Model={self.current_model_name}, Mode={'Night' if self.is_dark_mode else 'Day'}")
        except Exception as e:
            logger.info(f"Error logging performance stats: {e}")

    def get_performance_report(self) -> Dict:
        return {
            'camera_id': self.cam_id,
            'current_model': self.current_model_name,
            'is_dark_mode': self.is_dark_mode,
            'fps': self.performance_stats['current_fps'],
            'avg_detection_time_ms': self.performance_stats['avg_detection_time'] * 1000,
            'total_frames': self.performance_stats['total_frames'],
            'model_switches': self.performance_stats['model_switches']
        }

    def should_switch_to_night_mode(self, brightness: float) -> bool:
        from .config import DARK_THRESHOLD
        current_hour = datetime.now().hour
        is_night_time = current_hour < 6 or current_hour > 22
        return brightness < DARK_THRESHOLD or is_night_time

    def switch_model_if_needed(self, frame: np.ndarray):
        brightness = self.calculate_brightness(frame)
        should_be_night = self.should_switch_to_night_mode(brightness)
        prev_dark = self.is_dark_mode
        self.is_dark_mode = bool(should_be_night)
        self.current_model_name = "night" if self.is_dark_mode else "day"
        if prev_dark != self.is_dark_mode:
            state = "NIGHT" if self.is_dark_mode else "DAY"
            logger.info(f"Camera {self.cam_id}: Display mode -> {state} (brightness: {brightness:.2f})")

    def load_class_thresholds(self):
        from .config import reload_class_thresholds
        try:
            all_thresholds = reload_class_thresholds()
            camera_key = f"camera_{self.cam_id}"
            return all_thresholds.get(camera_key, {})
        except Exception as e:
            logger.info(f"Error loading class thresholds for camera {self.cam_id}: {e}")
            return {}

    def update_class_thresholds(self, thresholds: Dict[str, float]):
        try:
            self.class_thresholds.update(thresholds)
            logger.info(f"Updated thresholds for camera {self.cam_id}: {thresholds}")
        except Exception as e:
            logger.info(f"Error updating class thresholds for camera {self.cam_id}: {e}")

    def reset_class_thresholds(self):
        try:
            from .config import AI_CONF
            self.class_thresholds = {}
            logger.info(f"Reset thresholds to defaults for camera {self.cam_id}")
        except Exception as e:
            logger.info(f"Error resetting class thresholds for camera {self.cam_id}: {e}")

    def get_class_name(self, class_id: int) -> str:
        from basebuddy.core.inference.types import coco_class_name
        return coco_class_name(int(class_id))

    def get_class_name_from_id(self, class_id: int) -> str:
        return self.get_class_name(class_id)

    def _apply_tracking_overrides(self):
        """Apply persisted TRACKING_CONFIG overrides (global then per-camera)."""
        try:
            from .config import load_tracking_config
            stored = load_tracking_config()
            merged = {}
            merged.update(stored.get('global', {}) or {})
            merged.update((stored.get('cameras', {}) or {}).get(str(self.cam_id), {}) or {})
            key_map = {
                'max_age': 'max_track_age',
                'max_history': 'max_track_history',
                'cleanup_interval': 'track_cleanup_interval',
                'line_thickness': 'track_line_thickness',
                'line_length': 'track_line_length',
            }
            config = {key_map[k]: v for k, v in merged.items() if k in key_map}
            if config:
                self.update_tracking_config(config)
        except Exception as e:
            logger.warning(f"Could not apply tracking overrides for camera {self.cam_id}: {e}")

    def update_tracking_config(self, config: dict):
        if 'max_track_age' in config:
            self.max_track_age = int(config['max_track_age'])
        if 'max_track_history' in config:
            self.max_track_history = int(config['max_track_history'])
        if 'track_cleanup_interval' in config:
            self.track_cleanup_interval = int(config['track_cleanup_interval'])
        if 'track_line_thickness' in config:
            self.track_line_thickness = int(config['track_line_thickness'])
        if 'track_line_length' in config:
            self.track_line_length = int(config['track_line_length'])
        logger.info(f"Updated tracking config for camera {self.cam_id}: {config}")

    def reset_tracker(self):
        try:
            if self.tracker:
                import supervision as sv
                from .config import TRACKER_TYPE
                if TRACKER_TYPE == "bytetrack":
                    self.tracker = sv.ByteTrack()
                elif TRACKER_TYPE == "deepsort":
                    self.tracker = sv.ByteTrack()
                else:
                    self.tracker = sv.ByteTrack()
                self.track_history.clear()
                self.track_colors.clear()
                self.frame_count = 0
                logger.info(f"Reset tracker for camera {self.cam_id}")
        except Exception as e:
            logger.info(f"Error resetting tracker for camera {self.cam_id}: {e}")

    def cleanup_old_tracks(self):
        current_time = time.time()
        tracks_to_remove = []
        for track_id, track_points in self.track_history.items():
            if track_points:
                last_timestamp = track_points[-1][2] if len(track_points[0]) > 2 else current_time
                if current_time - last_timestamp > (self.max_track_age / 30.0):
                    tracks_to_remove.append(track_id)
        for track_id in tracks_to_remove:
            del self.track_history[track_id]
            if track_id in self.track_colors:
                del self.track_colors[track_id]
            if track_id in self.track_names:
                del self.track_names[track_id]
            self.track_classes.pop(track_id, None)

    def get_track_color(self, track_id) -> Tuple[int, int, int]:
        track_id = int(track_id)
        if track_id not in self.track_colors:
            import random
            random.seed(track_id)
            self.track_colors[track_id] = (
                random.randint(0, 255),
                random.randint(0, 255),
                random.randint(255//2, 255)
            )
        return self.track_colors[track_id]

    def update_track_history(self, detections):
        current_time = time.time()
        active_tracks = set()
        if hasattr(detections, 'tracker_id') and detections.tracker_id is not None:
            for i, track_id in enumerate(detections.tracker_id):
                if track_id is not None:
                    active_tracks.add(track_id)
                    box = detections.xyxy[i]
                    center_x = int((box[0] + box[2]) / 2)
                    center_y = int((box[1] + box[3]) / 2)
                    track_points = self.track_history[track_id]
                    track_points.append((center_x, center_y, current_time))
                    if len(track_points) > self.max_track_history:
                        track_points.pop(0)
                    if track_id not in self.track_classes:
                        try:
                            self.track_classes[track_id] = self.get_class_name(detections.class_id[i])
                        except Exception:
                            pass
        self.frame_count += 1
        if self.frame_count % self.track_cleanup_interval == 0:
            stale_before = time.time() - (self.max_track_age / 30.0)
            finalized_tracks = []
            for t_id, pts in list(self.track_history.items()):
                if not pts:
                    continue
                last_ts = pts[-1][2] if len(pts[0]) > 2 else time.time()
                if last_ts < stale_before:
                    finalized_tracks.append((t_id, pts.copy()))
                    del self.track_history[t_id]
            try:
                from .config import TRAFFIC_CAM_ID, PX_PER_M
            except Exception:
                TRAFFIC_CAM_ID = -1
                PX_PER_M = 50
            regions = []
            try:
                from basebuddy.core.regions import load_camera_regions, primary_analytics_label
                regions = load_camera_regions(self.cam_id)
            except Exception:
                pass
            fh, fw = (self._last_frame_shape[0], self._last_frame_shape[1]) if getattr(self, '_last_frame_shape', None) else (1080, 1920)
            has_analytics = any(r.get('analytics') and (r.get('label') or '').strip() for r in regions)
            if finalized_tracks and hasattr(self, 'analytics_db') and self.analytics_db:
                if has_analytics or self.cam_id == TRAFFIC_CAM_ID:
                    for t_id, pts in finalized_tracks:
                        cleaned = [(float(x), float(y), float(ts if ts else time.time())) for (x, y, ts) in pts if len(pts[0]) > 2]
                        if len(cleaned) >= 2:
                            try:
                                region_label = None
                                if has_analytics:
                                    x1, y1, _ = cleaned[0]
                                    fake_bbox = np.array([x1 - 1, y1 - 1, x1 + 1, y1 + 1])
                                    region_label = primary_analytics_label(
                                        fake_bbox, (fh, fw, 3), regions,
                                    )
                                    if not region_label:
                                        continue
                                self.analytics_db.save_traffic_track(
                                    self.cam_id, int(t_id), cleaned, int(PX_PER_M),
                                    region_label=region_label,
                                    class_name=self.track_classes.get(t_id),
                                )
                            except Exception:
                                pass
                for t_id, _pts in finalized_tracks:
                    try:
                        from basebuddy.core.services.event_session_service import get_event_session_service
                        get_event_session_service().on_track_end(self.cam_id, int(t_id))
                    except Exception:
                        pass
                    self.track_classes.pop(t_id, None)
            self.cleanup_old_tracks()
        return active_tracks

    def draw_tracking_paths(self, frame: np.ndarray) -> np.ndarray:
        tracks_drawn = 0
        for track_id, track_points in self.track_history.items():
            if len(track_points) < 2:
                continue
            recent_points = track_points[-self.track_line_length:]
            color = self.get_track_color(track_id)
            for i in range(1, len(recent_points)):
                pt1 = (int(recent_points[i-1][0]), int(recent_points[i-1][1]))
                pt2 = (int(recent_points[i][0]), int(recent_points[i][1]))
                cv2.line(frame, pt1, pt2, color, self.track_line_thickness)
            if recent_points:
                latest_point = recent_points[-1]
                cv2.putText(frame, f"ID:{track_id}", (int(latest_point[0]) + 5, int(latest_point[1]) - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
            tracks_drawn += 1
        if tracks_drawn > 0 and self.frame_count % 30 == 0:
            logger.info(f"Camera {self.cam_id}: Drew {tracks_drawn} tracking paths")
        return frame

    def detect_and_track(self, frame: np.ndarray) -> Tuple[np.ndarray, List]:
        if not self.detection_enabled:
            return frame, []
        self._last_frame_shape = frame.shape
        try:
            from basebuddy.core.inference import get_inference_router
            from basebuddy.core.inference.exceptions import ResourceExhausted

            self.switch_model_if_needed(frame)
            processed_frame = frame
            if self.is_dark_mode:
                processed_frame = self.enhance_night_frame(frame)

            try:
                inference_result = get_inference_router().detect(
                    frame,
                    camera_id=self.cam_id,
                    is_dark_mode=self.is_dark_mode,
                )
            except ResourceExhausted as exc:
                logger.warning(f"Camera {self.cam_id}: {exc}, skipping detection")
                try:
                    from .profiler import get_profiler
                    get_profiler().record_error(self.cam_id, is_resource_exhausted=True)
                except Exception:
                    pass
                return frame, []

            detection_time = inference_result.inference_ms / 1000.0
            self.update_performance_stats(detection_time)
            try:
                from .profiler import get_profiler
                get_profiler().record_detection(self.cam_id, inference_result.inference_ms)
            except Exception:
                pass

            if not inference_result.detections:
                return frame, []

            boxes = np.array([d.bbox.as_xyxy() for d in inference_result.detections])
            class_ids = np.array([d.class_id for d in inference_result.detections], dtype=int)
            confidences = np.array([d.confidence for d in inference_result.detections])
            if len(boxes) == 0 or len(class_ids) == 0 or len(confidences) == 0:
                return frame, []
            if len(boxes) != len(class_ids) or len(class_ids) != len(confidences):
                logger.info(f"Detection data shape mismatch: boxes={len(boxes)}, class_ids={len(class_ids)}, confidences={len(confidences)}")
                return frame, []
            high_conf_mask = []
            for i, (class_id, conf) in enumerate(zip(class_ids, confidences)):
                try:
                    class_name = self.get_class_name(int(class_id))
                    if class_name in self.disabled_classes:
                        high_conf_mask.append(False)
                        continue
                    if self.is_detection_ignored(boxes[i], class_name):
                        high_conf_mask.append(False)
                        continue
                    if self.is_filtered_by_roi(boxes[i], frame.shape):
                        high_conf_mask.append(False)
                        continue
                    # Check instance-level ignore via color histogram + IoU
                    if self.is_instance_ignored(boxes[i], class_name, frame):
                        high_conf_mask.append(False)
                        continue
                    from .config import AI_CONF
                    base_threshold = self.class_thresholds.get(class_name, AI_CONF)
                    base_threshold = float(base_threshold)
                    if self.is_dark_mode:
                        threshold = min(base_threshold, self.night_threshold)
                    else:
                        threshold = max(base_threshold, self.day_threshold)
                    high_conf_mask.append(bool(conf > threshold))
                except (ValueError, TypeError) as e:
                    logger.info(f"Error processing detection {i}: class_id={class_id}, conf={conf}, error={e}")
                    high_conf_mask.append(False)
            high_conf_mask = np.array(high_conf_mask, dtype=bool)
            try:
                boxes = boxes[high_conf_mask]
                class_ids = class_ids[high_conf_mask]
                confidences = confidences[high_conf_mask]
            except (IndexError, TypeError) as e:
                logger.info(f"Error applying detection filter mask: {e}")
                return frame, []
            if len(boxes) == 0:
                return frame, []
            import supervision as sv
            detections = sv.Detections(
                xyxy=boxes,
                class_id=class_ids,
                confidence=confidences
            )
            if self.tracker and len(detections) > 0:
                detections = self.tracker.update_with_detections(detections)
                active_tracks = self.update_track_history(detections)
                self.last_detections = []
                self._store_region_labels = []

                for i, (bbox, class_id, conf, track_id) in enumerate(zip(
                    detections.xyxy, detections.class_id, detections.confidence,
                    detections.tracker_id if detections.tracker_id is not None else [None] * len(detections)
                )):
                    class_name = self.get_class_name(int(class_id))
                    labels = self.process_region_metadata(
                        bbox, class_name, frame.shape,
                        confidence=float(conf),
                        track_id=int(track_id) if track_id is not None else None,
                    )
                    label_str = ','.join(labels) if labels else None
                    self._store_region_labels.append(label_str)
                    self.last_detections.append({
                        'bbox': bbox,
                        'class_name': class_name,
                        'confidence': float(conf),
                        'track_id': int(track_id) if track_id is not None else None,
                        'region_labels': labels,
                    })
                if self.frame_count % 30 == 0:
                    logger.info(f"Camera {self.cam_id}: {len(active_tracks)} active tracks, {len(self.track_history)} total tracks in history")
                frame = self.draw_tracking_paths(frame)
            else:
                self._store_region_labels = []
                for bbox, class_id, conf in zip(detections.xyxy, detections.class_id, detections.confidence):
                    class_name = self.get_class_name(int(class_id))
                    labels = self.process_region_metadata(
                        bbox, class_name, frame.shape, confidence=float(conf),
                    )
                    self._store_region_labels.append(','.join(labels) if labels else None)
            try:
                from .config import NIGHT_ENHANCEMENT
            except Exception:
                NIGHT_ENHANCEMENT = True
            display_frame = processed_frame if (self.is_dark_mode and NIGHT_ENHANCEMENT) else frame
            annotated_frame = self.draw_detections(display_frame, detections)
            return annotated_frame, detections
        except Exception as e:
            logger.info(f"Detection error for camera {self.cam_id}: {e}")
            return frame, []

    def draw_detections(self, frame: np.ndarray, detections) -> np.ndarray:
        if len(detections) == 0:
            return frame
        boxes = detections.xyxy
        class_ids = detections.class_id
        confidences = detections.confidence
        tracker_ids = getattr(detections, 'tracker_id', None)
        for i, (box, class_id, conf) in enumerate(zip(boxes, class_ids, confidences)):
            x1, y1, x2, y2 = map(int, box)
            tracker_id = None
            if tracker_ids is not None and i < len(tracker_ids):
                tracker_id = tracker_ids[i]
            if tracker_id is not None:
                color = self.get_track_color(tracker_id)
            else:
                if class_id == 0:
                    color = (0, 255, 0)
                elif class_id in [2, 7]:
                    color = (255, 165, 0)
                else:
                    color = (0, 165, 255)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 4)
            class_name = self.get_class_name(class_id)
            
            # Use recognized name if available
            label_prefix = class_name
            if tracker_id is not None and tracker_id in self.track_names:
                label_prefix = self.track_names[tracker_id]

            if tracker_id is not None:
                label = f"ID:{tracker_id} {label_prefix} {conf:.2f}"
            else:
                label = f"{label_prefix} {conf:.2f}"
            cv2.putText(frame, label, (x1, y1 - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 4)
        return frame
