#!/usr/bin/env python3
"""
BaseBuddy - AI-Powered Security Camera System
Clean modular entry point

Architecture:
- pages/         - UI pages (HTML routes + page-specific APIs)
- core/api/      - Core API endpoints (metrics, storage, etc.)
- core/services/ - Background services (backup, health monitor)
- modules/       - Shared business logic (camera, detection, database)
- plugins/       - Optional features (traffic counting, etc.)
"""
import logging

logger = logging.getLogger(__name__)

# Bootstrap repo/app roots when running basebuddy/main.py directly
import os as _os
import sys as _sys

_bb_dir = _os.path.dirname(_os.path.abspath(__file__))
_repo = _os.path.dirname(_bb_dir)
_os.environ.setdefault("BASEBUDDY_REPO_ROOT", _repo)
_os.environ.setdefault("BASEBUDDY_APP_ROOT", _bb_dir)
# The app is imported as the `basebuddy` package; its parent must be importable.
if _repo not in _sys.path:
    _sys.path.insert(0, _repo)

# Suppress FFmpeg/libav warnings
import os
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "loglevel;quiet"
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
os.environ["LIBAV_LOG_LEVEL"] = "quiet"

# PyTorch >= 2.6 defaults torch.load to weights_only=True, which breaks
# Ultralytics checkpoints (they pickle model classes). Allow full unpickling
# only for recognized model weight files instead of all .pt files, so a
# malicious .pt fetched from elsewhere is still loaded safely by default.
_TRUSTED_WEIGHT_PREFIXES = ("yolo", "rtdetr", "sam", "fastsam", "mobile_sam", "dust3r")


def _is_trusted_weight_file(file_path) -> bool:
    name = getattr(file_path, "name", file_path)
    if not isinstance(name, str) or not name.endswith((".pt", ".pth")):
        return False
    base = _os.path.basename(name).lower()
    return base.startswith(_TRUSTED_WEIGHT_PREFIXES)


try:
    import torch
    _original_torch_load = torch.load

    def _patched_torch_load(*args, **kwargs):
        if 'weights_only' not in kwargs and args and _is_trusted_weight_file(args[0]):
            kwargs['weights_only'] = False
        return _original_torch_load(*args, **kwargs)

    torch.load = _patched_torch_load
    logger.info("Patched torch.load for Ultralytics checkpoint compatibility (trusted weight files only)")
except Exception as e:
    logger.warning(f"Could not patch torch.load: {e}")

# Initialize CUDA context with PyTorch first
try:
    import torch
    if torch.cuda.is_available():
        _ = torch.zeros(1).cuda()
        logger.info("PyTorch CUDA context initialized")
except Exception:
    pass

# Set multiprocessing start method to 'spawn' (required for CUDA)
import multiprocessing
try:
    if hasattr(multiprocessing, 'set_start_method'):
        try:
            multiprocessing.set_start_method('spawn', force=False)
            logger.info("Multiprocessing start method set to 'spawn'")
        except RuntimeError:
            if multiprocessing.get_start_method() != 'spawn':
                logger.warning(
                    f"Multiprocessing start method is '{multiprocessing.get_start_method()}', not 'spawn'"
                )
            else:
                logger.info("Multiprocessing start method is 'spawn'")
except Exception as e:
    logger.warning(f"Could not set multiprocessing start method: {e}")

# Setup logging before creating app
from basebuddy.core.utils import setup_logging, setup_exception_handler, start_resource_monitor

logger = setup_logging(log_level='INFO')
setup_exception_handler(logger)
start_resource_monitor()

# Ensure AI models are available (auto-download if missing)
logger.info("=" * 60)
from basebuddy.modules.model_manager import ensure_models_available
from basebuddy.modules.config import AI_MODEL, DAY_MODEL, NIGHT_MODEL, ADAPTIVE_MODE

required_models = [AI_MODEL]
if ADAPTIVE_MODE:
    required_models.extend([DAY_MODEL, NIGHT_MODEL])

if not ensure_models_available(required_models):
    logger.warning("Warning: Some models failed to download. Detection may not work.")
    logger.info("   You can download models manually from:")
    logger.info("   https://github.com/ultralytics/assets/releases")
logger.info("=" * 60)

# Create Flask app using factory pattern
from basebuddy.app import create_app

app, socketio = create_app('default')

# Initialize shared state
import basebuddy.modules.state as shared_state
from basebuddy.modules.database import AnalyticsDB
from basebuddy.modules.config import (
    HOST, PORT, RECORD_ROOT, BACKUP_ENABLED, 
    BACKUP_DRIVE_PATH, BACKUP_FOLDER,
    BACKUP_INTERVAL_HOURS, BACKUP_MAX_AGE_HOURS,
    ARCHIVE_ENABLED, ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER,
    ARCHIVE_INTERVAL_DAYS, ARCHIVE_MIN_AGE_DAYS,
    MEDIA_BASE_DIR,
    STORAGE_QUOTA_GB,
    DISK_FREE_MIN_GB,
    RETENTION_SCAN_HOURS,
    RETENTION_POLICY_JSON,
    RETENTION_DAYS,
    REMOTE_STORAGE_ENABLED,
    REMOTE_STORAGE_PROVIDER,
    REMOTE_BUCKET,
    REMOTE_REGION,
    REMOTE_ENDPOINT,
    REMOTE_PREFIX,
    REMOTE_ACCESS_KEY,
    REMOTE_SECRET_KEY,
    BASEBUDDY_MANAGED_CLOUD_ENABLED,
    BASEBUDDY_CLOUD_API_URL,
    BASEBUDDY_CLOUD_API_KEY,
)

from basebuddy.core.paths import get_repo_root as _get_repo_root

_project_root = _get_repo_root()

from basebuddy.core.storage_paths import abs_under_project
from basebuddy.core.storage_paths import build_archive_source_dirs, build_retention_source_dirs
from basebuddy.core.storage_policy_runtime import build_user_s3_backend
from basebuddy.core.premium_hooks import resolve_remote_backend, reload_managed_cloud_from_config
from basebuddy.core.retention_config import retention_policy_from_env
from basebuddy.core.services.object_storage import S3ObjectStorage


# Initialize analytics database
if not shared_state.analytics_db:
    shared_state.analytics_db = AnalyticsDB()
    logger.info("Analytics database initialized")

# Initialize backup manager
from basebuddy.core.services import BackupManager

_record_root_abs = abs_under_project(_project_root, RECORD_ROOT)

if shared_state.backup_manager is None:
    shared_state.backup_manager = BackupManager(
        record_root=_record_root_abs,
        backup_drive_path=BACKUP_DRIVE_PATH,
        backup_folder=BACKUP_FOLDER,
        backup_interval_hours=BACKUP_INTERVAL_HOURS,
        backup_max_age_hours=BACKUP_MAX_AGE_HOURS,
        enabled=BACKUP_ENABLED
    )
    shared_state.backup_manager.start()
    logger.info("Backup manager initialized")

# Initialize archive service (periodic move-to-external-drive + local cleanup)
from basebuddy.core.services import ArchiveService

if not shared_state.archive_service:
    shared_state.archive_service = ArchiveService(
        source_dirs=build_archive_source_dirs(_project_root, RECORD_ROOT, MEDIA_BASE_DIR),
        archive_drive_path=ARCHIVE_DRIVE_PATH,
        archive_folder=ARCHIVE_FOLDER,
        interval_days=ARCHIVE_INTERVAL_DAYS,
        min_age_days=ARCHIVE_MIN_AGE_DAYS,
        enabled=ARCHIVE_ENABLED,
        storage_quota_gb=STORAGE_QUOTA_GB,
    )
    shared_state.archive_service.start()
    logger.info("Archive service initialized")

# Per-service retention + optional cloud offload
from basebuddy.core.services import RetentionService

if not shared_state.retention_service:
    _retention_dirs = build_retention_source_dirs(_project_root, RECORD_ROOT, MEDIA_BASE_DIR)
    _policy = retention_policy_from_env(RETENTION_POLICY_JSON, RETENTION_DAYS)
    _user_backend = S3ObjectStorage(
        enabled=REMOTE_STORAGE_ENABLED,
        provider=REMOTE_STORAGE_PROVIDER,
        bucket=REMOTE_BUCKET,
        access_key=REMOTE_ACCESS_KEY,
        secret_key=REMOTE_SECRET_KEY,
        region=REMOTE_REGION,
        endpoint_url=REMOTE_ENDPOINT,
        prefix=REMOTE_PREFIX,
    )
    _remote_backend, _remote_kind = resolve_remote_backend(_user_backend)
    shared_state.retention_service = RetentionService(
        source_dirs=_retention_dirs,
        retention_policy=_policy,
        remote_backend=_remote_backend,
        remote_backend_kind=_remote_kind,
        scan_interval_seconds=max(1, RETENTION_SCAN_HOURS) * 3600,
        enabled=True,
        disk_free_min_gb=DISK_FREE_MIN_GB,
    )
    shared_state.retention_service.start()
    logger.info("Retention service initialized")

reload_managed_cloud_from_config()

# Initialize health monitor
from basebuddy.core.services import HealthMonitor

if not shared_state.health_monitor:
    shared_state.health_monitor = HealthMonitor(
        grabbers_ref=shared_state.grabbers,
        record_root=_record_root_abs,
    )
    shared_state.health_monitor.start()
    logger.info("Health monitor initialized")

# Initialize camera grabbers and detectors
try:
    from basebuddy.modules.system_init import init_all
    logger.info("Initializing camera grabbers and detectors...")
    init_all()
    logger.info("Camera system initialized")
except Exception as e:
    logger.error(f"Failed to initialize camera system: {e}", exc_info=True)
    logger.warning(f"Camera initialization failed: {e}")

# Home scenes background scheduler
try:
    from basebuddy.modules.config import HOME_SCENES_ENABLE
    if HOME_SCENES_ENABLE:
        from basebuddy.plugins.home_scenes.scheduler import get_scene_scheduler
        get_scene_scheduler().start()
        logger.info("Home scenes scheduler started")
except Exception as e:
    logger.warning(f"Home scenes scheduler not started: {e}")

# Print startup info
logger.info("=" * 60)
logger.info("BaseBuddy application initialized successfully")
logger.info("")
logger.info("Modular Pages (pages/):")
logger.info("  - / (Camera Wall)")
logger.info("  - /camera/<id> (Camera Detail)")
logger.info("  - /recordings")
logger.info("  - /timelapse")
logger.info("  - /gallery")
logger.info("  - /events (review timeline)")
logger.info("  - /integrations (MQTT, alerts, performance)")
logger.info("  - /config (+ /setup, /thresholds, /tracking, /disabled-classes)")
logger.info("  - /storage (disk policy & backup/archive)")
logger.info("  - /scenes (pantry / fridge monitoring)")
logger.info("  - /plants (plant health vision monitoring)")
logger.info("  - /metrics (system analytics)")
logger.info("")
logger.info("Core APIs (core/api/):")
logger.info("  - /api/metrics/*")
logger.info("  - /api/storage/*")
logger.info("  - /api/backup/*")
logger.info("  - /api/wall/*")
logger.info("  - /api/gallery/*")
logger.info("=" * 60)


from basebuddy.core.shutdown import register_shutdown_handlers, shutdown_basebuddy

register_shutdown_handlers()


if __name__ == "__main__":
    logger.info(f"\n Starting BaseBuddy on http://{HOST}:{PORT}\n")

    try:
        if socketio:
            # Flask-SocketIO with async_mode='threading' runs on Werkzeug, which
            # is a development server. allow_unsafe_werkzeug acknowledges that;
            # this app is designed for a single-tenant LAN deployment. For an
            # internet-facing deployment, put a reverse proxy (nginx/caddy) with
            # TLS in front and enable AUTH_ENABLE + ADMIN_PASSWORD.
            socketio.run(
                app,
                host=HOST,
                port=PORT,
                debug=False,
                use_reloader=False,
                allow_unsafe_werkzeug=True,
            )
        else:
            app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        shutdown_basebuddy()
