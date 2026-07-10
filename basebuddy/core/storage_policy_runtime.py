"""
Apply storage / backup / archive / retention settings from config to running services.
"""
import importlib
import logging
from typing import Any, Dict

import basebuddy.modules.state as app_state
from basebuddy.core.premium_hooks import resolve_remote_backend
from basebuddy.core.retention_config import normalize_retention_policy, retention_policy_from_env
from basebuddy.core.services.object_storage import S3ObjectStorage
from basebuddy.core.storage_paths import abs_under_project, build_archive_source_dirs, build_retention_source_dirs, project_root_from_here

logger = logging.getLogger("basebuddy")


def _premium_installed() -> bool:
    try:
        import importlib

        importlib.import_module("basebuddy_premium")
        return True
    except ImportError:
        return False


def _reload_config_module():
    import basebuddy.modules.config as cfg

    importlib.reload(cfg)
    return cfg


def build_user_s3_backend(cfg) -> S3ObjectStorage:
    return S3ObjectStorage(
        enabled=cfg.REMOTE_STORAGE_ENABLED,
        provider=cfg.REMOTE_STORAGE_PROVIDER,
        bucket=cfg.REMOTE_BUCKET,
        access_key=cfg.REMOTE_ACCESS_KEY,
        secret_key=cfg.REMOTE_SECRET_KEY,
        region=cfg.REMOTE_REGION,
        endpoint_url=cfg.REMOTE_ENDPOINT,
        prefix=cfg.REMOTE_PREFIX,
    )


def apply_runtime_storage_policy() -> Dict[str, Any]:
    """Reload config and push values into backup, archive, and retention services."""
    cfg = _reload_config_module()
    project_root = project_root_from_here()

    from basebuddy.core.premium_hooks import reload_managed_cloud_from_config

    reload_managed_cloud_from_config()

    record_root = cfg.RECORD_ROOT
    media_base = cfg.MEDIA_BASE_DIR
    source_dirs = build_archive_source_dirs(project_root, record_root, media_base)
    retention_dirs = build_retention_source_dirs(project_root, record_root, media_base)
    retention_policy = retention_policy_from_env(cfg.RETENTION_POLICY_JSON, cfg.RETENTION_DAYS)

    user_backend = build_user_s3_backend(cfg)
    remote_backend, remote_kind = resolve_remote_backend(user_backend)

    summary: Dict[str, Any] = {"ok": True, "errors": [], "remote_backend": remote_kind}

    bm = app_state.backup_manager
    if bm is not None:
        try:
            bm.apply_runtime_settings(
                record_root=abs_under_project(project_root, record_root),
                backup_drive_path=cfg.BACKUP_DRIVE_PATH,
                backup_folder=cfg.BACKUP_FOLDER,
                backup_interval_hours=cfg.BACKUP_INTERVAL_HOURS,
                backup_max_age_hours=cfg.BACKUP_MAX_AGE_HOURS,
                enabled=cfg.BACKUP_ENABLED,
            )
        except Exception as exc:
            logger.exception("BackupManager apply_runtime_settings: %s", exc)
            summary["errors"].append(f"backup: {exc}")

    ar = app_state.archive_service
    if ar is not None:
        try:
            quota = getattr(cfg, "STORAGE_QUOTA_GB", 0) or 0
            try:
                quota_f = float(quota)
            except (TypeError, ValueError):
                quota_f = 0.0
            ar.apply_runtime_settings(
                source_dirs=source_dirs,
                archive_drive_path=cfg.ARCHIVE_DRIVE_PATH,
                archive_folder=cfg.ARCHIVE_FOLDER,
                interval_days=cfg.ARCHIVE_INTERVAL_DAYS,
                min_age_days=cfg.ARCHIVE_MIN_AGE_DAYS,
                enabled=cfg.ARCHIVE_ENABLED,
                storage_quota_gb=quota_f,
            )
        except Exception as exc:
            logger.exception("ArchiveService apply_runtime_settings: %s", exc)
            summary["errors"].append(f"archive: {exc}")

    rs = app_state.retention_service
    if rs is not None:
        try:
            scan_s = max(1, int(getattr(cfg, "RETENTION_SCAN_HOURS", 1))) * 3600
            rs.apply_runtime_settings(
                source_dirs=retention_dirs,
                retention_policy=retention_policy,
                remote_backend=remote_backend,
                remote_backend_kind=remote_kind,
                enabled=True,
                disk_free_min_gb=float(getattr(cfg, "DISK_FREE_MIN_GB", 20) or 0),
            )
            rs.scan_interval_seconds = scan_s
        except Exception as exc:
            logger.exception("RetentionService apply_runtime_settings: %s", exc)
            summary["errors"].append(f"retention: {exc}")

    if summary["errors"]:
        summary["ok"] = False
    return summary


def current_settings_snapshot() -> Dict[str, Any]:
    """Effective settings from modules.config (no reload)."""
    from basebuddy.modules import config as cfg

    project_root = project_root_from_here()
    policy = retention_policy_from_env(cfg.RETENTION_POLICY_JSON, cfg.RETENTION_DAYS)
    user_backend = build_user_s3_backend(cfg)
    remote_backend, remote_kind = resolve_remote_backend(user_backend)

    snap = {
        "RECORD_ROOT": cfg.RECORD_ROOT,
        "RETENTION_DAYS": cfg.RETENTION_DAYS,
        "RETENTION_POLICY": policy,
        "RETENTION_SCAN_HOURS": getattr(cfg, "RETENTION_SCAN_HOURS", 1),
        "BACKUP_ENABLED": cfg.BACKUP_ENABLED,
        "BACKUP_DRIVE_PATH": cfg.BACKUP_DRIVE_PATH,
        "BACKUP_FOLDER": cfg.BACKUP_FOLDER,
        "BACKUP_INTERVAL_HOURS": cfg.BACKUP_INTERVAL_HOURS,
        "BACKUP_MAX_AGE_HOURS": cfg.BACKUP_MAX_AGE_HOURS,
        "ARCHIVE_ENABLED": cfg.ARCHIVE_ENABLED,
        "ARCHIVE_DRIVE_PATH": cfg.ARCHIVE_DRIVE_PATH,
        "ARCHIVE_FOLDER": cfg.ARCHIVE_FOLDER,
        "ARCHIVE_INTERVAL_DAYS": cfg.ARCHIVE_INTERVAL_DAYS,
        "ARCHIVE_MIN_AGE_DAYS": cfg.ARCHIVE_MIN_AGE_DAYS,
        "STORAGE_QUOTA_GB": getattr(cfg, "STORAGE_QUOTA_GB", 0),
        "DISK_FREE_MIN_GB": getattr(cfg, "DISK_FREE_MIN_GB", 20),
        "MEDIA_BASE_DIR": cfg.MEDIA_BASE_DIR,
        "REMOTE_STORAGE_ENABLED": cfg.REMOTE_STORAGE_ENABLED,
        "REMOTE_STORAGE_PROVIDER": cfg.REMOTE_STORAGE_PROVIDER,
        "REMOTE_BUCKET": cfg.REMOTE_BUCKET,
        "REMOTE_REGION": cfg.REMOTE_REGION,
        "REMOTE_ENDPOINT": cfg.REMOTE_ENDPOINT,
        "REMOTE_PREFIX": cfg.REMOTE_PREFIX,
        "REMOTE_ACCESS_KEY": cfg.REMOTE_ACCESS_KEY,
        "REMOTE_SECRET_KEY_SET": bool(cfg.REMOTE_SECRET_KEY),
        "REMOTE_SECRET_KEY_MASK": S3ObjectStorage.mask_secret(cfg.REMOTE_SECRET_KEY),
        "remote_backend_active": remote_kind,
        "BASEBUDDY_MANAGED_CLOUD_ENABLED": cfg.BASEBUDDY_MANAGED_CLOUD_ENABLED,
        "BASEBUDDY_CLOUD_API_URL": cfg.BASEBUDDY_CLOUD_API_URL,
        "BASEBUDDY_CLOUD_API_KEY": cfg.BASEBUDDY_CLOUD_API_KEY,
        "BASEBUDDY_CLOUD_API_KEY_SET": bool(cfg.BASEBUDDY_CLOUD_API_KEY),
        "BASEBUDDY_CLOUD_API_KEY_MASK": S3ObjectStorage.mask_secret(cfg.BASEBUDDY_CLOUD_API_KEY),
        "premium_package_installed": _premium_installed(),
        "archive_source_dirs": build_archive_source_dirs(
            project_root, cfg.RECORD_ROOT, cfg.MEDIA_BASE_DIR
        ),
        "retention_source_dirs": build_retention_source_dirs(
            project_root, cfg.RECORD_ROOT, cfg.MEDIA_BASE_DIR
        ),
    }
    return snap
