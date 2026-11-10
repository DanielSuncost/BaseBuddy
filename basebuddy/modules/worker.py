"""
Child-process worker for detection (multiprocessing target).
"""
from types import SimpleNamespace
import logging
import time

logger = logging.getLogger(__name__)

_last_result_drop_log = 0.0


def _emit_detection_result(tx_results, payload, timeout: float = 0.25) -> bool:
    """Hand a result to the parent without stalling the worker.

    A blocking ``put`` on a full result queue deadlocks detection: the parent
    stops draining while it is busy, the worker blocks forever inside ``put``,
    and no further frames are inferred until the process is restarted.
    """
    global _last_result_drop_log
    try:
        tx_results.put(payload, timeout=timeout)
        return True
    except Exception:
        now = time.time()
        if now - _last_result_drop_log > 5.0:
            _last_result_drop_log = now
            logger.warning(
                "Detection result queue full; dropping result so detection can continue"
            )
        return False

def _detection_worker_process(cam_id: int, rx_frames, tx_results, pose_queue=None):
    """Child process: create local detector, run detection, return annotated frames and counts."""
    import time as _t
    try:
        from .detection import DetectionTracker
        from .recognition import FaceRecognizer
        import numpy as np
        
        worker = DetectionTracker(cam_id)
        
        # Initialize Face Recognition
        try:
            recognizer = FaceRecognizer()
            if recognizer.enabled:
                worker.set_classifier(recognizer)
        except Exception as e:
            logger.error(f"Worker {cam_id}: Failed to init FaceRecognizer: {e}")

        # NOTE: pose_queue is passed but may not work across process boundaries
        # If pose_queue is None or causes issues, pose detection will be skipped
        # This is safer than crashing the entire detection worker
        try:
            if pose_queue is not None:
                worker.set_pose_queue(pose_queue)
        except Exception as e:
            logger.info(f"Worker {cam_id}: Could not set pose_queue (will skip pose detection): {e}")
            worker.pose_queue = None

        while True:
            msg = rx_frames.get()
            if not msg:
                continue
            if msg.get('cmd') == 'stop':
                break
            if msg.get('cmd') == 'frame':
                ts = msg['ts']
                frame = msg['frame']
                try:
                    annotated, detections = worker.detect_and_track(frame)
                    det_count = len(detections) if hasattr(detections, '__len__') else 0
                    boxes = detections.xyxy.tolist() if hasattr(detections, 'xyxy') else []
                    classes = detections.class_id.tolist() if hasattr(detections, 'class_id') else []
                    confs = detections.confidence.tolist() if hasattr(detections, 'confidence') else []
                    track_ids = []
                    if hasattr(detections, 'tracker_id') and detections.tracker_id is not None:
                        track_ids = [
                            int(t) if t is not None else None
                            for t in detections.tracker_id.tolist()
                        ]
                    _emit_detection_result(tx_results, {
                        'cmd': 'result',
                        'ts': ts,
                        'frame': annotated,
                        'det_count': det_count,
                        'det': {
                            'boxes': boxes,
                            'classes': classes,
                            'confs': confs,
                            'track_ids': track_ids,
                        },
                    })
                except Exception as e:
                    # Log the error for debugging
                    logger.error(f"Worker {cam_id}: Detection error: {e}")
                    import traceback
                    traceback.print_exc()
                    # Return unannotated frame on error
                    _emit_detection_result(tx_results, {'cmd':'result', 'ts': ts, 'frame': frame, 'det_count': 0})
    except KeyboardInterrupt:
        pass
    except Exception:
        _t.sleep(0.1)



