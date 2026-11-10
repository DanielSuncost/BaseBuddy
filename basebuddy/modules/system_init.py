from basebuddy.modules.config import (
    CAM_URLS,
    BUFFER_MAX_FRAMES,
    YOLO_AVAILABLE,
    DETECTION_ENABLED,
    BACKUP_ENABLED,
    BACKUP_DRIVE_PATH,
)
from basebuddy.modules.models import FrameGrabber, DetectionTracker
from basebuddy.modules.camera_profiles import get_profile_manager
import basebuddy.modules.state as app_state
from basebuddy.modules.state import grabbers, detectors, analytics_db
import logging

logger = logging.getLogger(__name__)


def _profile_wants_detection(camera_id: int) -> bool:
    """Return True if this camera should run YOLO (global + per-camera profile)."""
    if not (YOLO_AVAILABLE and DETECTION_ENABLED):
        return False
    try:
        profile = get_profile_manager().get_profile(camera_id)
        return bool(profile.camera_enabled and profile.detection_enabled)
    except Exception:
        # Fail open only when profile lookup fails; defaults enable detection.
        return True


def _apply_detection_fps(grabber, profile) -> float:
    """Apply profile.detection_fps to grabber idle/active cadence. Returns clamped fps."""
    from basebuddy.modules.config import DETECTION_ACTIVE_FPS, DETECTION_FPS_MIN, DETECTION_FPS_MAX

    try:
        fps = float(getattr(profile, "detection_fps", 1.0) or 1.0)
    except (TypeError, ValueError):
        fps = 1.0
    fps = max(DETECTION_FPS_MIN, min(fps, DETECTION_FPS_MAX))
    grabber.idle_detection_fps = fps
    # Boost when objects are present, but keep at least the user's requested rate.
    grabber.active_detection_fps = min(
        DETECTION_FPS_MAX, max(fps * 2.0, float(DETECTION_ACTIVE_FPS or fps))
    )
    return fps


def apply_runtime_profile(camera_id: int, profile=None) -> None:
    """Apply camera_enabled / detection_enabled / detection_fps to a running grabber+detector.

    Video-only cameras must not enqueue frames or keep a live YOLO detector.
    """
    if profile is None:
        profile = get_profile_manager().get_profile(camera_id)

    want_detection = bool(
        YOLO_AVAILABLE
        and DETECTION_ENABLED
        and profile.camera_enabled
        and profile.detection_enabled
    )

    try:
        fps = float(getattr(profile, "detection_fps", 1.0) or 1.0)
    except (TypeError, ValueError):
        fps = 1.0
    from basebuddy.modules.config import DETECTION_FPS_MIN, DETECTION_FPS_MAX
    fps = max(DETECTION_FPS_MIN, min(fps, DETECTION_FPS_MAX))
    try:
        from basebuddy.modules.power_management import get_power_manager
        get_power_manager().set_camera_ai_fps(camera_id, fps if want_detection else None)
    except Exception:
        pass

    grabber = grabbers.get(camera_id)
    if grabber is not None:
        grabber.camera_enabled = bool(profile.camera_enabled)
        grabber.detection_enabled = want_detection
        _apply_detection_fps(grabber, profile)
        if not want_detection:
            try:
                grabber.detection_queue.clear()
            except Exception:
                pass
            with grabber.lock:
                grabber.last_detections = []
                grabber.last_det_count = 0
                grabber.last_detection_ts = 0.0
                grabber.last_detection_frame_ts = 0.0
                grabber.last_annotated_frame = None

    detector = detectors.get(camera_id)
    if want_detection:
        if detector is None:
            try:
                detector = DetectionTracker(camera_id)
                detectors[camera_id] = detector
                detector.analytics_db = analytics_db
                logger.info(f"Camera {camera_id + 1}: Detector created (detection enabled)")
            except Exception as e:
                logger.error(f"Camera {camera_id + 1}: Detector failed - {e}")
                detector = None
        else:
            detector.detection_enabled = True

        if grabber is not None and detector is not None:
            grabber.detector = detector
            grabber.analytics_db = analytics_db
    else:
        if detector is not None:
            detector.detection_enabled = False
            try:
                if hasattr(detector, "clear_tracks"):
                    detector.clear_tracks()
                if hasattr(detector, "release_models"):
                    detector.release_models()
            except Exception as e:
                logger.warning(f"Camera {camera_id + 1}: Error releasing detector: {e}")
            detectors.pop(camera_id, None)
        if grabber is not None:
            grabber.detector = None

    fps_note = ""
    if grabber is not None and want_detection:
        fps_note = f", detection_fps={getattr(grabber, 'idle_detection_fps', '?')}"
    logger.info(
        f"Camera {camera_id + 1}: Camera {'ON' if profile.camera_enabled else 'OFF'}, "
        f"Detection {'ON' if want_detection else 'OFF'}{fps_note}"
    )


def init_all():
    """Initialize all camera grabbers and detectors"""
    logger.info("Initializing BaseBuddy...")

    if not DETECTION_ENABLED:
        logger.info("DETECTION_ENABLED=false — skipping detector initialization")

    profile_manager = get_profile_manager()

    # First, initialize detectors only for cameras that want AI
    for i, url in enumerate(CAM_URLS):
        if not url:
            continue
        if _profile_wants_detection(i):
            try:
                detector = DetectionTracker(i)
                detectors[i] = detector
                logger.info(f"Camera {i+1}: Detector initialized")
            except Exception as e:
                logger.error(f"Camera {i+1}: Detector failed - {e}")
        else:
            logger.info(f"Camera {i+1}: Video-only / detection disabled — skipping YOLO")

    # Then, initialize all frame grabbers and link them to detectors
    for i, url in enumerate(CAM_URLS):
        if not url:
            logger.warning(f"Camera {i+1}: No URL configured")
            continue

        # Initialize frame grabber
        grabber = FrameGrabber(i, url, BUFFER_MAX_FRAMES)

        try:
            profile = profile_manager.get_profile(i)
            grabber.camera_enabled = bool(profile.camera_enabled)
            grabber.detection_enabled = bool(i in detectors and profile.detection_enabled)
            detection_fps = _apply_detection_fps(grabber, profile)
            try:
                from basebuddy.modules.power_management import get_power_manager
                get_power_manager().set_camera_ai_fps(
                    i, detection_fps if grabber.detection_enabled else None
                )
            except Exception:
                pass
        except Exception:
            grabber.detection_enabled = i in detectors

        # Set up detector and analytics for background processing
        if i in detectors:
            grabber.detector = detectors[i]
            grabber.analytics_db = analytics_db
            # Also allow detector to persist traffic tracks directly
            try:
                detectors[i].analytics_db = analytics_db
            except Exception:
                pass

        grabber.start()
        grabbers[i] = grabber
        mode = "background detection" if grabber.detection_enabled else "video only"
        logger.info(f"Camera {i+1}: Grabber initialized ({mode})")

    logger.info(f"System ready: {len(grabbers)} cameras, {len(detectors)} detectors")

    # Backup manager is created in main.py; avoid stale imports of backup_manager.
    bm = app_state.backup_manager
    if bm:
        if BACKUP_ENABLED:
            if bm.is_backup_drive_available():
                logger.info(f"Backup system ready - Path: {bm.backup_path}")
            else:
                logger.warning(f"Backup drive not available - Path: {BACKUP_DRIVE_PATH}")
                logger.info("Please ensure your USB backup drive is mounted and accessible")
        bm.start()


def add_single_camera(cam_id: int, url: str) -> bool:
    """Add a single camera without reinitializing everything else. Uses shared state (grabbers, detectors)."""
    logger.info(f"[HOT-ADD] Adding camera {cam_id + 1} with URL: {url[:50]}...")

    existing = grabbers.get(cam_id)
    if existing is not None:
        same_url = (getattr(existing, "url", None) or "") == (url or "")
        if same_url and getattr(existing, "running", False):
            logger.info(f"[HOT-ADD] Camera {cam_id + 1} already running")
            return True
        logger.info(
            "[HOT-ADD] Camera %s restarting (%s)",
            cam_id + 1,
            "url changed" if not same_url else "not running",
        )
        try:
            existing.stop()
        except Exception:
            pass
        grabbers.pop(cam_id, None)
        detectors.pop(cam_id, None)

    try:
        want_detection = _profile_wants_detection(cam_id)
        if want_detection:
            detector = DetectionTracker(cam_id)
            detector.set_pose_queue(None)
            detectors[cam_id] = detector
            logger.info(f"[HOT-ADD] Camera {cam_id + 1}: Detector created")
        else:
            logger.info(f"[HOT-ADD] Camera {cam_id + 1}: Video-only — skipping YOLO")

        grabber = FrameGrabber(cam_id, url, BUFFER_MAX_FRAMES, pose_queue=None)
        try:
            profile = get_profile_manager().get_profile(cam_id)
            grabber.camera_enabled = bool(profile.camera_enabled)
            detection_fps = _apply_detection_fps(grabber, profile)
            try:
                from basebuddy.modules.power_management import get_power_manager
                get_power_manager().set_camera_ai_fps(
                    cam_id, detection_fps if want_detection else None
                )
            except Exception:
                pass
        except Exception:
            grabber.camera_enabled = True
        grabber.detection_enabled = want_detection

        if cam_id in detectors:
            grabber.detector = detectors[cam_id]
            grabber.analytics_db = analytics_db
            detectors[cam_id].analytics_db = analytics_db

        grabber.start()
        grabbers[cam_id] = grabber

        logger.info(f"[HOT-ADD]  Camera {cam_id + 1} added and started")
        return True

    except Exception as e:
        logger.error(f"[HOT-ADD]  Failed to add camera {cam_id + 1}: {e}")
        import traceback
        traceback.print_exc()
        return False


