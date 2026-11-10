"""
Detection pipeline: in-thread and child-process (IPC) detection processing,
adaptive detection cadence, and a watchdog that restarts a stalled worker
in-process.
"""
import logging
import threading
import time
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np

from basebuddy.core.services.detection_watchdog import (
    DETECTION_STALE_S,
    DETECTION_WATCHDOG_INTERVAL_S,
    is_detection_stale,
    result_age_s,
)
from basebuddy.modules.config import MULTIPROC_DETECTION, JPEG_QUALITY
from basebuddy.modules.worker import _detection_worker_process

from .environment import mp, _spawn_context

logger = logging.getLogger(__name__)


class DetectionPipelineMixin:
    """Detection scheduling and result handling for FrameGrabber."""

    def start_detection_thread(self):
        """Start detection processing: thread in-process or child process."""
        if MULTIPROC_DETECTION and mp is not None:
            if not (self.proc and self.proc.is_alive()):
                self._start_detection_process()
        else:
            if not (self.detection_thread and self.detection_thread.is_alive()):
                self._spawn_inprocess_worker()
        self._ensure_detection_watchdog()

    def _start_detection_process(self):
        """Spawn a child-process detector plus the in-process IPC pump."""
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
        generation = int(getattr(self, "_detection_generation", 0))
        self._detection_worker_started_ts = time.time()
        self.detection_thread = threading.Thread(
            target=self._process_detection_ipc,
            args=(generation,),
            name=f"detection-ipc-cam-{self.cam_id}",
            daemon=True,
        )
        self.detection_thread.start()

    def _spawn_inprocess_worker(self):
        """Start the in-process detection loop bound to the current generation."""
        generation = int(getattr(self, "_detection_generation", 0))
        self.detection_running = True
        self._detection_worker_started_ts = time.time()
        self.detection_thread = threading.Thread(
            target=self._process_detection_queue,
            args=(generation,),
            name=f"detection-cam-{self.cam_id}",
            daemon=True,
        )
        self.detection_thread.start()

    def _ensure_detection_watchdog(self):
        if self.watchdog_thread and self.watchdog_thread.is_alive():
            return
        self.watchdog_thread = threading.Thread(
            target=self._detection_watchdog,
            name=f"det-watchdog-cam-{self.cam_id}",
            daemon=True,
        )
        self.watchdog_thread.start()

    def stop_detection_thread(self):
        self._detection_generation = int(getattr(self, "_detection_generation", 0)) + 1
        self.detection_running = False
        thread = self.detection_thread
        if thread and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=1.0)
        self._stop_detection_process(force=False)
        if self.watchdog_thread:
            # Best-effort; daemon thread exits when self.running is false
            self.watchdog_thread = None

    def _stop_detection_process(self, force: bool = False):
        """Stop the child detector. Never block on a full frame queue."""
        proc = self.proc
        tx = self.tx
        if proc is None:
            self.tx = None
            self.rx = None
            return
        if tx is not None:
            try:
                tx.put_nowait({'cmd': 'stop'})
            except Exception:
                pass
        try:
            if proc.is_alive():
                if force:
                    proc.terminate()
                proc.join(timeout=1.0)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=1.0)
        except Exception:
            pass
        # Drop the queues without join_thread(); joining a feeder thread that
        # is blocked on a full pipe deadlocks the caller.
        self.proc = None
        self.tx = None
        self.rx = None

    def _detection_loop_active(self, generation: int) -> bool:
        return bool(self.detection_running) and generation == int(getattr(self, "_detection_generation", 0))

    def _pop_latest_detection_frame(self):
        """Pop the newest queued frame, discarding older backlog entries."""
        frame, timestamp = self.detection_queue.popleft()
        while self.detection_queue:
            frame, timestamp = self.detection_queue.popleft()
        return frame, timestamp

    def _process_detection_queue(self, generation: int = None):
        """Background thread for processing detection queue"""
        if generation is None:
            generation = int(getattr(self, "_detection_generation", 0))
        while self._detection_loop_active(generation):
            try:
                if not getattr(self, 'detection_enabled', True):
                    if self.detection_queue:
                        self.detection_queue.clear()
                    time.sleep(0.05)
                    continue

                if self.detection_queue:
                    # Always detect on the newest frame — never burn GPU on a backlog.
                    frame, timestamp = self._pop_latest_detection_frame()
                    if self.detector:
                        try:
                            frame_start_time = time.time()
                            processed_frame, detections = self.detector.detect_and_track(frame.copy())
                            if not self._detection_loop_active(generation):
                                return
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
                                # Wall-clock completion time keeps overlays visible after
                                # slow inference; frame ts is used separately for staleness.
                                self.last_detection_ts = time.time()
                                self.last_detection_frame_ts = float(timestamp or 0.0)
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
                                    elif self.last_det_count == 0:
                                        self.last_detections = []
                                except Exception:
                                    if self.last_det_count == 0:
                                        self.last_detections = []
                            try:
                                self._record_wall_trails(self.last_detections)
                            except Exception:
                                pass

                            if not self._detection_loop_active(generation):
                                return

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

    def _process_detection_ipc(self, generation: int = None):
        """Background thread when using a child process: send frames and read results."""
        if generation is None:
            generation = int(getattr(self, "_detection_generation", 0))
        consecutive_empty = 0
        while self._detection_loop_active(generation):
            try:
                # Optimized: Non-blocking operations with adaptive sleep
                has_work = False

                if not getattr(self, 'detection_enabled', True):
                    if self.detection_queue:
                        self.detection_queue.clear()
                    time.sleep(0.05)
                    continue

                if self.detection_queue and self.tx is not None:
                    try:
                        frame, ts = self._pop_latest_detection_frame()
                        try:
                            self.tx.put_nowait({'cmd': 'frame', 'ts': ts, 'frame': frame})
                            self.pending_frames[ts] = frame
                            # Bound pending frames in case the worker stops returning results
                            while len(self.pending_frames) > 60:
                                self.pending_frames.pop(next(iter(self.pending_frames)))
                            has_work = True
                        except Exception:
                            # Queue full or worker gone. Drop the frame — putting
                            # it back busy-spins the pump and can block shutdown
                            # on a queue nobody is reading.
                            now_full = time.time()
                            if now_full - float(getattr(self, "_tx_full_log_ts", 0) or 0) > 5.0:
                                self._tx_full_log_ts = now_full
                                logger.warning(
                                    "Camera %s: detection frame queue full; dropping frame",
                                    self.cam_id,
                                )
                            time.sleep(0.05)
                    except IndexError:
                        pass  # Queue empty

                if self.rx is not None:
                    try:
                        msg = self.rx.get_nowait()
                        has_work = True
                        if msg and msg.get('cmd') == 'result':
                            if not self._detection_loop_active(generation):
                                return
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
                                self.last_detection_ts = time.time()
                                self.last_detection_frame_ts = float(ts or 0.0)
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
                                    if det_count <= 0 or det_payload is None:
                                        self.last_detections = []
                                    elif self.detector is not None:
                                        boxes = det_payload.get('boxes') or []
                                        classes = det_payload.get('classes') or []
                                        confs = det_payload.get('confs') or []
                                        track_ids = det_payload.get('track_ids') or []
                                        ld = []
                                        for i in range(min(len(boxes), len(classes), len(confs))):
                                            box = boxes[i]
                                            class_id = int(classes[i])
                                            conf = float(confs[i])
                                            class_name = self.detector.get_class_name_from_id(class_id) if hasattr(self.detector, 'get_class_name_from_id') else str(class_id)
                                            tid = track_ids[i] if i < len(track_ids) else None
                                            if tid is not None:
                                                try:
                                                    tid = int(tid)
                                                except (TypeError, ValueError):
                                                    tid = None
                                            ld.append({
                                                'bbox': box,
                                                'class_name': class_name,
                                                'confidence': conf,
                                                'track_id': tid
                                            })
                                        self.last_detections = ld
                                        try:
                                            self._record_wall_trails(ld)
                                        except Exception:
                                            pass
                                except Exception:
                                    if det_count <= 0:
                                        self.last_detections = []
                            if not self._detection_loop_active(generation):
                                return
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

    def _detection_is_stale(self, now: float) -> bool:
        try:
            depth = len(self.detection_queue)
        except Exception:
            depth = 0
        return is_detection_stale(
            now=now,
            detection_enabled=bool(getattr(self, "detection_enabled", False)),
            running=bool(getattr(self, "running", False)),
            worker_started_ts=float(getattr(self, "_detection_worker_started_ts", 0) or 0),
            last_detection_ts=float(getattr(self, "last_detection_ts", 0) or 0),
            last_frame_ts=float(getattr(self, "_last_successful_read", 0) or 0),
            last_restart_ts=float(getattr(self, "_last_detection_restart_ts", 0) or 0),
            last_enqueue_ts=float(getattr(self, "_last_detection_enqueue_ts", 0) or 0),
            queue_depth=depth,
            stale_after_s=DETECTION_STALE_S,
        )

    def _restart_stalled_detection(self, idle_s: float) -> None:
        """Replace a wedged detection worker without restarting the process."""
        if not getattr(self, "running", False):
            return
        abandoned = getattr(self, "_abandoned_detection_thread", None)
        if abandoned is not None and not abandoned.is_alive():
            self._abandoned_detection_thread = None
            abandoned = None
        if abandoned is not None and abandoned.is_alive():
            now = time.time()
            if now - float(getattr(self, "_stall_persist_log_ts", 0) or 0) > 60.0:
                self._stall_persist_log_ts = now
                logger.warning(
                    "Camera %s: detection still stalled for %.1fs after in-process restart; "
                    "previous worker has not exited",
                    self.cam_id, idle_s,
                )
            return

        last_ts = float(getattr(self, "last_detection_ts", 0) or 0)
        if last_ts > 0:
            when = f"{result_age_s(last_ts, 0.0, time.time()):.1f}s ago"
        else:
            when = "never"
        logger.warning(
            "Camera %s: detection stalled for %.1fs (last detection %s); "
            "restarting detection worker in-process",
            self.cam_id, idle_s, when,
        )
        self._detection_generation = int(getattr(self, "_detection_generation", 0)) + 1
        self._last_detection_restart_ts = time.time()
        self._stop_detection_process(force=True)
        previous = self.detection_thread
        if previous is not None and previous is not threading.current_thread() and previous.is_alive():
            previous.join(timeout=1.0)
            if previous.is_alive():
                logger.warning(
                    "Camera %s: abandoning stuck detection worker and starting a replacement",
                    self.cam_id,
                )
                self._abandoned_detection_thread = previous
        try:
            self.detection_queue.clear()
        except Exception:
            pass
        try:
            self.pending_frames.clear()
        except Exception:
            self.pending_frames = {}
        self.proc = None
        self.tx = None
        self.rx = None
        self._spawn_inprocess_worker()

    def _detection_watchdog(self):
        """Restart in-process detection when results stop while capture continues.

        Runs for both the child-process and in-thread workers. A single fallback
        that then exits cannot recover a later stall; this loop stays up until
        the grabber stops.
        """
        while getattr(self, "running", False):
            time.sleep(DETECTION_WATCHDOG_INTERVAL_S)
            if not getattr(self, "running", False):
                break
            try:
                now = time.time()
                if not self._detection_is_stale(now):
                    continue
                last_ts = float(getattr(self, "last_detection_ts", 0) or 0)
                started = float(getattr(self, "_detection_worker_started_ts", 0) or 0)
                age = result_age_s(last_ts, started, now) or 0.0
                self._restart_stalled_detection(age)
            except Exception:
                logger.exception("Camera %s: detection watchdog error", self.cam_id)

    def _get_detection_interval(self) -> Optional[float]:
        """Return current target detection interval based on activity."""
        if self.active_detection_fps > 0 and (time.time() - self._last_detection_activity_ts) < self.active_detection_window:
            return 1.0 / self.active_detection_fps
        if self.idle_detection_fps > 0:
            return 1.0 / self.idle_detection_fps
        return None

    def _should_enqueue_detection(self, now_ts: float) -> bool:
        """Decide whether to enqueue a frame for detection."""
        # Grabber-level flag is authoritative (profile video-only / runtime apply).
        if not getattr(self, 'detection_enabled', True):
            return False

        detector_enabled = False
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
