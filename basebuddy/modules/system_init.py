from basebuddy.modules.config import (
    CAM_URLS,
    BUFFER_MAX_FRAMES,
    YOLO_AVAILABLE,
    DETECTION_ENABLED,
    BACKUP_ENABLED,
    BACKUP_DRIVE_PATH,
)
from basebuddy.modules.models import FrameGrabber, DetectionTracker
import basebuddy.modules.state as app_state
from basebuddy.modules.state import grabbers, detectors, analytics_db
import logging

logger = logging.getLogger(__name__)


def init_all():
    """Initialize all camera grabbers and detectors"""
    logger.info("Initializing BaseBuddy...")

    # First, initialize all detectors
    if not DETECTION_ENABLED:
        logger.info("DETECTION_ENABLED=false — skipping detector initialization")
    for i, url in enumerate(CAM_URLS):
        if not url:
            continue
        if YOLO_AVAILABLE and DETECTION_ENABLED:
            try:
                detector = DetectionTracker(i)
                detectors[i] = detector
                logger.info(f"Camera {i+1}: Detector initialized")
            except Exception as e:
                logger.error(f"Camera {i+1}: Detector failed - {e}")

    # Then, initialize all frame grabbers and link them to detectors
    for i, url in enumerate(CAM_URLS):
        if not url:
            logger.warning(f"Camera {i+1}: No URL configured")
            continue

        # Initialize frame grabber
        grabber = FrameGrabber(i, url, BUFFER_MAX_FRAMES)

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
        logger.info(f"Camera {i+1}: Grabber initialized with background detection")

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

    # Skip if already exists and running
    if cam_id in grabbers and getattr(grabbers[cam_id], "running", False):
        logger.info(f"[HOT-ADD] Camera {cam_id + 1} already running")
        return True

    # Stop existing grabber if present
    if cam_id in grabbers:
        try:
            grabbers[cam_id].stop()
        except Exception:
            pass

    try:
        if YOLO_AVAILABLE and DETECTION_ENABLED:
            detector = DetectionTracker(cam_id)
            detector.set_pose_queue(None)
            detectors[cam_id] = detector
            logger.info(f"[HOT-ADD] Camera {cam_id + 1}: Detector created")

        grabber = FrameGrabber(cam_id, url, BUFFER_MAX_FRAMES, pose_queue=None)
        grabber.camera_enabled = True

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


