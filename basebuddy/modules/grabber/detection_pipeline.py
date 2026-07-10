"""
Detection pipeline: in-thread and child-process (IPC) detection processing,
adaptive detection cadence, and the child-process watchdog.
"""
import logging
import threading
import time
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np

from basebuddy.modules.config import MULTIPROC_DETECTION, JPEG_QUALITY
from basebuddy.modules.worker import _detection_worker_process

from .environment import mp, _spawn_context

logger = logging.getLogger(__name__)


class DetectionPipelineMixin:
    """Detection scheduling and result handling for FrameGrabber."""

    def start_detection_thread(self):
        """Start detection processing: thread in-process or child process."""
        if MULTIPROC_DETECTION and mp is not None:
            if self.proc and self.proc.is_alive():
                return
            # Ensure we're using spawn context queues and processes
            # Use the spawn context that was set at module import time
            if _spawn_context is not None:
                Queue = _spawn_context.Queue
                Process = _spawn_context.Process
            else:
                # Fallback to default if spawn context not available
                try:
                    mp_context = mp.get_context('spawn')
                    Queue = mp_context.Queue
                    Process = mp_context.Process
                except Exception:
                    Queue = mp.Queue
                    Process = mp.Process
            self.tx, self.rx = Queue(maxsize=50), Queue(maxsize=50)  # Larger buffers for smoother processing
            # NOTE: pose_queue cannot be safely passed to spawned processes due to semaphore issues
            # Pose detection will be disabled in multiprocessing mode
            # Pass None instead to avoid semaphore rebuild errors
            self.proc = Process(target=_detection_worker_process, args=(self.cam_id, self.tx, self.rx, None), daemon=True)
            self.proc.start()
            self.detection_running = True
            self.detection_thread = threading.Thread(target=self._process_detection_ipc, daemon=True)
            self.detection_thread.start()
            # Start watchdog to auto-fallback if no results arrive
            if not self.watchdog_thread or not self.watchdog_thread.is_alive():
                self.watchdog_thread = threading.Thread(target=self._detection_watchdog, daemon=True)
                self.watchdog_thread.start()
        else:
            if self.detection_thread and self.detection_thread.is_alive():
                return
            self.detection_running = True
            self.detection_thread = threading.Thread(target=self._process_detection_queue, daemon=True)
            self.detection_thread.start()

    def stop_detection_thread(self):
        self.detection_running = False
        if self.detection_thread:
            self.detection_thread.join(timeout=1.0)
        if self.proc and self.proc.is_alive():
            try:
                self.tx.put({'cmd': 'stop'})
            except Exception:
                pass
            self.proc.join(timeout=2.0)
            if self.proc.is_alive():
                # Don't leak the child process if it ignores the stop command
                try:
                    self.proc.terminate()
                    self.proc.join(timeout=1.0)
                except Exception:
                    pass
        if self.watchdog_thread:
            # Best-effort; daemon thread
            self.watchdog_thread = None

    def _process_detection_queue(self):
        """Background thread for processing detection queue"""
        while self.detection_running:
            try:
                if self.detection_queue:
                    frame, timestamp = self.detection_queue.popleft()
                    if self.detector:
                        try:
                            frame_start_time = time.time()
                            processed_frame, detections = self.detector.detect_and_track(frame.copy())
                            frame_processing_time = (time.time() - frame_start_time) * 1000.0  # ms

                            # Record frame processing time
                            try:
                                from basebuddy.modules.profiler import get_profiler
                                profiler = get_profiler()
                                profiler.record_frame_processing(self.cam_id, frame_processing_time)
                                profiler.record_queue_depth(self.cam_id, len(self.detection_queue))
                            except Exception:
                                pass  # Profiler not critical

                            with self.lock:
                                self.last_annotated_frame = processed_frame
                                self.last_det_count = int(len(detections)) if hasattr(detections, '__len__') else 0
                                self.last_detection_ts = timestamp
                                if self.last_det_count > 0:
                                    self._mark_detection_activity()

                                # Pre-encode JPEG in detection thread (CPU optimization)
                                try:
                                    stream_quality = max(75, JPEG_QUALITY - 5)
                                    encode_params = [
                                        int(cv2.IMWRITE_JPEG_QUALITY), stream_quality,
                                        cv2.IMWRITE_JPEG_OPTIMIZE, 1,
                                    ]
                                    ok, jpg = cv2.imencode(".jpg", processed_frame, encode_params)
                                    if ok:
                                        self.latest_detection_jpeg_bytes = jpg.tobytes()
                                        self.latest_detection_jpeg_ts = timestamp
                                        self.latest_annotated_frame = processed_frame
                                        self.latest_annotated_ts = timestamp
                                except Exception:
                                    pass  # Encoding failed, but don't block detection

                                # Mirror detector's last_detections for endpoint use
                                try:
                                    if hasattr(self.detector, 'last_detections'):
                                        self.last_detections = list(self.detector.last_detections)
                                except Exception:
                                    pass

                            if len(detections) > 0 and self.analytics_db:
                                region_labels = getattr(self.detector, '_store_region_labels', None)
                                self.analytics_db.store_event(
                                    self.cam_id, detections, frame,
                                    self.detector.get_class_name_from_id,
                                    region_labels=region_labels,
                                )
                        except Exception as e:
                            logger.info(f"Detection error for camera {self.cam_id}: {e}")
                else:
                    time.sleep(0.01)
            except Exception as e:
                logger.info(f"Detection thread error for camera {self.cam_id}: {e}")
                time.sleep(0.1)

    def _process_detection_ipc(self):
        """Background thread when using a child process: send frames and read results."""
        consecutive_empty = 0
        while self.detection_running:
            try:
                # Optimized: Non-blocking operations with adaptive sleep
                has_work = False

                if self.detection_queue and self.tx is not None:
                    try:
                        frame, ts = self.detection_queue.popleft()
                        try:
                            self.tx.put_nowait({'cmd': 'frame', 'ts': ts, 'frame': frame})
                            self.pending_frames[ts] = frame
                            # Bound pending frames in case the worker stops returning results
                            while len(self.pending_frames) > 60:
                                self.pending_frames.pop(next(iter(self.pending_frames)))
                            has_work = True
                        except Exception:
                            # Queue full, put frame back at front
                            self.detection_queue.appendleft((frame, ts))
                    except IndexError:
                        pass  # Queue empty

                if self.rx is not None:
                    try:
                        msg = self.rx.get_nowait()
                        has_work = True
                        if msg and msg.get('cmd') == 'result':
                            ts = msg['ts']
                            processed_frame = msg['frame']
                            det_count = int(msg.get('det_count', 0))
                            det_payload = msg.get('det')
                            raw_frame = self.pending_frames.pop(ts, None)
                            with self.lock:
                                self.last_annotated_frame = processed_frame
                                # Optimized: Direct reference for video stream (no lookup needed)
                                self.latest_annotated_frame = processed_frame
                                self.latest_annotated_ts = ts
                                self.last_det_count = int(det_count)
                                self.last_detection_ts = ts
                                if self.last_det_count > 0:
                                    self._mark_detection_activity()

                                # Pre-encode JPEG in detection thread (CPU optimization)
                                # Encode every frame for smooth video
                                try:
                                    stream_quality = max(75, JPEG_QUALITY - 5)
                                    encode_params = [
                                        int(cv2.IMWRITE_JPEG_QUALITY), stream_quality,
                                        cv2.IMWRITE_JPEG_OPTIMIZE, 1,
                                    ]
                                    ok, jpg = cv2.imencode(".jpg", processed_frame, encode_params)
                                    if ok:
                                        self.latest_detection_jpeg_bytes = jpg.tobytes()
                                        self.latest_detection_jpeg_ts = ts
                                except Exception:
                                    pass  # Encoding failed, but don't block detection

                                # Build last_detections from payload if available
                                try:
                                    if det_payload is not None and self.detector is not None:
                                        boxes = det_payload.get('boxes') or []
                                        classes = det_payload.get('classes') or []
                                        confs = det_payload.get('confs') or []
                                        ld = []
                                        for i in range(min(len(boxes), len(classes), len(confs))):
                                            box = boxes[i]
                                            class_id = int(classes[i])
                                            conf = float(confs[i])
                                            class_name = self.detector.get_class_name_from_id(class_id) if hasattr(self.detector, 'get_class_name_from_id') else str(class_id)
                                            ld.append({
                                                'bbox': box,
                                                'class_name': class_name,
                                                'confidence': conf,
                                                'track_id': None
                                            })
                                        self.last_detections = ld
                                except Exception:
                                    pass
                            try:
                                if det_payload and self.analytics_db:
                                    boxes = np.array(det_payload.get('boxes', []), dtype=float)
                                    classes = np.array(det_payload.get('classes', []), dtype=int)
                                    confs = np.array(det_payload.get('confs', []), dtype=float)
                                    if len(boxes) > 0:
                                        fake_det = SimpleNamespace(
                                            xyxy=boxes,
                                            class_id=classes,
                                            confidence=confs,
                                            tracker_id=None
                                        )
                                        if raw_frame is not None:
                                            self.analytics_db.store_event(self.cam_id, fake_det, raw_frame, self.detector.get_class_name_from_id)
                            except Exception:
                                pass
                    except Exception:
                        pass

                # Optimized: Adaptive sleep based on work availability
                if has_work:
                    consecutive_empty = 0
                    time.sleep(0.001)  # 1ms when busy
                else:
                    consecutive_empty += 1
                    if consecutive_empty > 10:
                        time.sleep(0.01)   # 10ms when idle for a while
                    else:
                        time.sleep(0.002)   # 2ms when temporarily idle
            except Exception as e:
                logger.info(f"IPC pump error cam {self.cam_id}: {e}")
                time.sleep(0.05)

    def _detection_watchdog(self):
        """Monitor detection results; fallback to in-thread detection if child is unresponsive."""
        # Grace period: measure idle time from watchdog start, not epoch 0,
        # so the child has time to load model weights before the first check.
        started = time.time()
        while self.running:
            try:
                if self.proc and self.proc.is_alive():
                    last_seen = max(float(self.last_detection_ts or 0), started)
                    idle_s = time.time() - last_seen
                    if idle_s > 30.0:
                        logger.warning(f"Camera {self.cam_id}: No detection results for {idle_s:.1f}s, falling back to in-thread detection")
                        try:
                            # Stop child
                            self.detection_running = False
                            if self.proc and self.proc.is_alive():
                                try:
                                    self.tx.put({'cmd': 'stop'})
                                except Exception:
                                    pass
                                self.proc.join(timeout=2.0)
                                if self.proc.is_alive():
                                    self.proc.terminate()
                                    self.proc.join(timeout=1.0)
                        except Exception:
                            pass
                        self.proc = None
                        self.tx = None
                        self.rx = None
                        # Start in-thread detection
                        if self.detection_thread and self.detection_thread.is_alive():
                            try:
                                self.detection_thread.join(timeout=0.5)
                            except Exception:
                                pass
                        self.detection_running = True
                        self.detection_thread = threading.Thread(target=self._process_detection_queue, daemon=True)
                        self.detection_thread.start()
                        return
                time.sleep(1.0)
            except Exception:
                time.sleep(1.0)

    def _get_detection_interval(self) -> Optional[float]:
        """Return current target detection interval based on activity."""
        if self.active_detection_fps > 0 and (time.time() - self._last_detection_activity_ts) < self.active_detection_window:
            return 1.0 / self.active_detection_fps
        if self.idle_detection_fps > 0:
            return 1.0 / self.idle_detection_fps
        return None

    def _should_enqueue_detection(self, now_ts: float) -> bool:
        """Decide whether to enqueue a frame for detection."""
        detector_enabled = True
        if self.detector and hasattr(self.detector, 'detection_enabled'):
            detector_enabled = bool(self.detector.detection_enabled)
        if not detector_enabled:
            return False

        interval = self._get_detection_interval()
        if interval is None:
            return False

        if (now_ts - self._last_detection_enqueue_ts) < interval:
            return False

        self._last_detection_enqueue_ts = now_ts
        return True

    def _mark_detection_activity(self):
        """Record that detections were recently observed."""
        self._last_detection_activity_ts = time.time()
