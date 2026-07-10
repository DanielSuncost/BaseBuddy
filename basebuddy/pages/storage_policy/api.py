"""
JSON API for storage policy: status, settings persistence, drive tests, manual runs.
"""
import os
from flask import jsonify, request

from basebuddy.core.config_persist import upsert_config_exports
from basebuddy.core.premium_hooks import get_edition_info
from basebuddy.core.retention_config import normalize_retention_policy, retention_policy_to_json
from basebuddy.core.retention_defaults import RETENTION_SERVICE_LABELS
from basebuddy.core.storage_estimate import estimate_storage, estimate_retention_plan
from basebuddy.core.storage_forecast import build_storage_forecast
from basebuddy.core.storage_rate_tracker import build_category_rates
from basebuddy.core.storage_paths import project_root_from_here
from basebuddy.core.storage_policy_runtime import (
    apply_runtime_storage_policy,
    build_user_s3_backend,
    current_settings_snapshot,
)
from basebuddy.core.services.object_storage import S3ObjectStorage
from basebuddy.core.storage_usage import (
    build_usage_breakdown,
    category_sizes,
    cloud_category_sizes,
    disk_usage_path,
    quota_status,
)
from basebuddy.modules.config import load_config_file
from basebuddy.pages.storage_policy import storage_policy_bp


def _state():
    import basebuddy.modules.state as app_state

    return app_state


def _test_write(path: str) -> dict:
    if not path or not os.path.isdir(path):
        return {"ok": False, "error": "Path does not exist or is not a directory"}
    probe = os.path.join(path, ".basebuddy_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        ok, du = disk_usage_path(path)
        return {
            "ok": True,
            "path": path,
            "disk": du if ok else {},
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


@storage_policy_bp.route("/api/storage-policy/edition", methods=["GET"])
def api_edition():
    try:
        info = get_edition_info()
        return jsonify({"ok": True, "data": info})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/estimate", methods=["POST"])
def api_estimate():
    try:
        body = request.get_json(silent=True) or {}
        if body.get("local_days") or body.get("cloud_days"):
            edition = get_edition_info()
            tiers = (edition.get("managed_cloud") or {}).get("pricing_tiers") or []
            body["pricing_tiers"] = tiers
            result = estimate_retention_plan(body)
        else:
            result = estimate_storage(body)
        return jsonify({"ok": True, "data": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/context", methods=["GET"])
def api_storage_context():
    """Camera count and timelapse hints for the storage planner UI."""
    try:
        from basebuddy.modules.config import CAM_URLS

        camera_count = len([u for u in CAM_URLS if u])
        still_interval_sec = 60
        timelapse_note = "Using default capture interval (60s). Configure schedules on the Timelapse page."
        try:
            from basebuddy.pages.timelapse.api import load_schedules

            schedules = load_schedules() or []
            active = [s for s in schedules if s.get("enabled", True)]
            if active:
                # Use smallest interval_hours converted to seconds between captures if present
                intervals = []
                for s in active:
                    ih = s.get("interval_hours")
                    if ih:
                        intervals.append(int(float(ih) * 3600))
                    fs = s.get("frame_skip") or 1
                    if fs and fs > 1:
                        still_interval_sec = max(still_interval_sec, fs)
                if intervals:
                    still_interval_sec = min(intervals)
                names = ", ".join(s.get("name") or f"Schedule {s.get('id')}" for s in active[:3])
                extra = f" (+{len(active) - 3} more)" if len(active) > 3 else ""
                timelapse_note = f"From timelapse schedules: {names}{extra}"
        except Exception:
            pass

        return jsonify(
            {
                "ok": True,
                "data": {
                    "camera_count": camera_count,
                    "still_interval_sec": still_interval_sec,
                    "timelapse_note": timelapse_note,
                    "timelapse_url": "/timelapse",
                },
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/retention-services", methods=["GET"])
def api_retention_services():
    try:
        snap = current_settings_snapshot()
        services = []
        for key, label in RETENTION_SERVICE_LABELS.items():
            cfg = (snap.get("RETENTION_POLICY") or {}).get(key, {})
            services.append(
                {
                    "id": key,
                    "label": label,
                    "local_days": cfg.get("local_days", 0),
                    "remote_days": cfg.get("remote_days", 0),
                }
            )
        return jsonify({"ok": True, "data": services})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/status", methods=["GET"])
def api_status():
    """Combined usage, quota, backup + archive + retention service status."""
    try:
        snap = current_settings_snapshot()
        src = snap["retention_source_dirs"]
        sizes = category_sizes(src)
        used = sum(sizes.values())
        quota_gb = float(snap.get("STORAGE_QUOTA_GB") or 0)
        quota = quota_status(quota_gb, used)

        bm = _state().backup_manager
        ar = _state().archive_service
        rs = _state().retention_service
        backup_status = bm.get_status() if bm else None
        archive_status = ar.get_status() if ar else None
        retention_status = rs.get_status() if rs else None

        root = project_root_from_here()
        ok_sys, sys_disk = disk_usage_path(root)

        backup_du = disk_usage_path(snap["BACKUP_DRIVE_PATH"])
        archive_du = disk_usage_path(snap["ARCHIVE_DRIVE_PATH"])

        cloud_usage = {}
        remote_backend = None
        remote_kind = snap.get("remote_backend_active", "none")
        remote_active = False

        if remote_kind == "managed":
            try:
                from basebuddy.core.premium_hooks import get_managed_cloud_backend

                remote_backend = get_managed_cloud_backend()
                remote_active = bool(
                    remote_backend and getattr(remote_backend, "is_active", False)
                )
                if remote_active:
                    cloud_usage = remote_backend.fetch_usage() or {}
            except Exception:
                remote_active = False
        elif remote_kind == "byo":
            from basebuddy.modules import config as cfg

            remote_backend = build_user_s3_backend(cfg)
            remote_active = bool(remote_backend.is_active)
            if remote_active:
                cloud_usage = remote_backend.fetch_usage() or {}

        cloud_sizes = cloud_category_sizes(remote_backend) if remote_active else {}
        usage_breakdown = build_usage_breakdown(
            sizes,
            cloud_sizes,
            snap.get("RETENTION_POLICY") or {},
            RETENTION_SERVICE_LABELS,
            remote_active=remote_active,
        )
        usage_breakdown["cloud_backend"] = remote_kind
        if not remote_active:
            usage_breakdown["cloud_note"] = (
                "Cloud upload is off — enable BaseBuddy Cloud or your own bucket below to track remote usage."
            )

        from basebuddy.modules.config import CAM_URLS, DB_PATH

        camera_count = len([u for u in CAM_URLS if u])
        policy = snap.get("RETENTION_POLICY") or {}
        category_rates = build_category_rates(
            root,
            src,
            sizes,
            policy,
            db_path=DB_PATH,
        )
        forecast = build_storage_forecast(
            category_sizes=sizes,
            category_rates=category_rates,
            retention_policy=policy,
            system_disk=sys_disk if ok_sys else {},
            camera_count=camera_count,
        )

        return jsonify(
            {
                "ok": True,
                "local": {
                    "categories_gb": {
                        k: round(v / (1024**3), 3) for k, v in sizes.items()
                    },
                    "total_gb": round(used / (1024**3), 3),
                    "project_root": root,
                },
                "usage_breakdown": usage_breakdown,
                "storage_forecast": forecast,
                "write_rates": {k: v for k, v in category_rates.items() if not k.startswith("_")},
                "quota": quota,
                "cloud_usage": cloud_usage,
                "system_disk": sys_disk if ok_sys else {},
                "backup_drive_disk": backup_du[1] if backup_du[0] else {},
                "archive_drive_disk": archive_du[1] if archive_du[0] else {},
                "backup": backup_status,
                "archive": archive_status,
                "retention": retention_status,
                "remote_backend": snap.get("remote_backend_active", "none"),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/settings", methods=["GET"])
def api_get_settings():
    try:
        return jsonify({"ok": True, "data": current_settings_snapshot()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _coerce_settings_body(data: dict) -> dict:
    """Validate and stringify values for config.txt."""
    out = {}
    if "RECORD_ROOT" in data and data["RECORD_ROOT"] is not None:
        out["RECORD_ROOT"] = str(data["RECORD_ROOT"]).strip()
    if "RETENTION_DAYS" in data and data["RETENTION_DAYS"] is not None:
        v = int(data["RETENTION_DAYS"])
        if v < 0:
            raise ValueError("RETENTION_DAYS must be >= 0")
        out["RETENTION_DAYS"] = str(v)
    if "RETENTION_POLICY" in data and data["RETENTION_POLICY"] is not None:
        policy = normalize_retention_policy(data["RETENTION_POLICY"])
        out["RETENTION_POLICY"] = retention_policy_to_json(policy)
        if "recordings" in policy:
            out["RETENTION_DAYS"] = str(policy["recordings"]["local_days"])
    if "RETENTION_SCAN_HOURS" in data and data["RETENTION_SCAN_HOURS"] is not None:
        out["RETENTION_SCAN_HOURS"] = str(max(1, int(data["RETENTION_SCAN_HOURS"])))
    if "BACKUP_ENABLED" in data:
        out["BACKUP_ENABLED"] = "true" if data["BACKUP_ENABLED"] else "false"
    if "BACKUP_DRIVE_PATH" in data:
        out["BACKUP_DRIVE_PATH"] = str(data["BACKUP_DRIVE_PATH"]).strip()
    if "BACKUP_FOLDER" in data:
        out["BACKUP_FOLDER"] = str(data["BACKUP_FOLDER"]).strip()
    if "BACKUP_INTERVAL_HOURS" in data and data["BACKUP_INTERVAL_HOURS"] is not None:
        out["BACKUP_INTERVAL_HOURS"] = str(max(1, int(data["BACKUP_INTERVAL_HOURS"])))
    if "BACKUP_MAX_AGE_HOURS" in data and data["BACKUP_MAX_AGE_HOURS"] is not None:
        out["BACKUP_MAX_AGE_HOURS"] = str(max(1, int(data["BACKUP_MAX_AGE_HOURS"])))
    if "ARCHIVE_ENABLED" in data:
        out["ARCHIVE_ENABLED"] = "true" if data["ARCHIVE_ENABLED"] else "false"
    if "ARCHIVE_DRIVE_PATH" in data:
        out["ARCHIVE_DRIVE_PATH"] = str(data["ARCHIVE_DRIVE_PATH"]).strip()
    if "ARCHIVE_FOLDER" in data:
        out["ARCHIVE_FOLDER"] = str(data["ARCHIVE_FOLDER"]).strip()
    if "ARCHIVE_INTERVAL_DAYS" in data and data["ARCHIVE_INTERVAL_DAYS"] is not None:
        out["ARCHIVE_INTERVAL_DAYS"] = str(max(1, int(data["ARCHIVE_INTERVAL_DAYS"])))
    if "ARCHIVE_MIN_AGE_DAYS" in data and data["ARCHIVE_MIN_AGE_DAYS"] is not None:
        out["ARCHIVE_MIN_AGE_DAYS"] = str(max(0, int(data["ARCHIVE_MIN_AGE_DAYS"])))
    if "STORAGE_QUOTA_GB" in data:
        q = data["STORAGE_QUOTA_GB"]
        if q is None or q == "":
            out["STORAGE_QUOTA_GB"] = "0"
        else:
            out["STORAGE_QUOTA_GB"] = str(max(0.0, float(q)))
    if "DISK_FREE_MIN_GB" in data:
        d = data["DISK_FREE_MIN_GB"]
        if d is None or d == "":
            out["DISK_FREE_MIN_GB"] = "20"
        else:
            out["DISK_FREE_MIN_GB"] = str(max(0.0, float(d)))
    if "REMOTE_STORAGE_ENABLED" in data:
        out["REMOTE_STORAGE_ENABLED"] = "true" if data["REMOTE_STORAGE_ENABLED"] else "false"
    if "REMOTE_STORAGE_PROVIDER" in data:
        out["REMOTE_STORAGE_PROVIDER"] = str(data["REMOTE_STORAGE_PROVIDER"]).strip().lower()
    if "REMOTE_BUCKET" in data:
        out["REMOTE_BUCKET"] = str(data["REMOTE_BUCKET"]).strip()
    if "REMOTE_REGION" in data:
        out["REMOTE_REGION"] = str(data["REMOTE_REGION"]).strip()
    if "REMOTE_ENDPOINT" in data:
        out["REMOTE_ENDPOINT"] = str(data["REMOTE_ENDPOINT"]).strip()
    if "REMOTE_PREFIX" in data:
        out["REMOTE_PREFIX"] = str(data["REMOTE_PREFIX"]).strip()
    if "REMOTE_ACCESS_KEY" in data:
        out["REMOTE_ACCESS_KEY"] = str(data["REMOTE_ACCESS_KEY"]).strip()
    secret = data.get("REMOTE_SECRET_KEY")
    if secret is not None and str(secret).strip() != "":
        out["REMOTE_SECRET_KEY"] = str(secret).strip()
    if "BASEBUDDY_MANAGED_CLOUD_ENABLED" in data:
        out["BASEBUDDY_MANAGED_CLOUD_ENABLED"] = (
            "true" if data["BASEBUDDY_MANAGED_CLOUD_ENABLED"] else "false"
        )
    if "BASEBUDDY_CLOUD_API_URL" in data:
        out["BASEBUDDY_CLOUD_API_URL"] = str(data["BASEBUDDY_CLOUD_API_URL"]).strip()
    if "BASEBUDDY_CLOUD_API_KEY" in data:
        key = str(data["BASEBUDDY_CLOUD_API_KEY"]).strip()
        if key:
            out["BASEBUDDY_CLOUD_API_KEY"] = key
    return out


@storage_policy_bp.route("/api/storage-policy/settings", methods=["PUT", "POST"])
def api_put_settings():
    try:
        body = request.get_json(silent=True) or {}
        updates = _coerce_settings_body(body)
        if not updates:
            return jsonify({"ok": False, "error": "No valid fields"}), 400
        root = project_root_from_here()
        upsert_config_exports(root, updates)
        load_config_file()
        result = apply_runtime_storage_policy()
        return jsonify({"ok": result.get("ok", True), "apply": result, "saved": updates})
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/test-path", methods=["POST"])
def api_test_path():
    try:
        body = request.get_json(silent=True) or {}
        path = (body.get("path") or "").strip()
        return jsonify({"ok": True, "result": _test_write(path)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/test-managed", methods=["POST"])
def api_test_managed():
    try:
        from basebuddy_premium.managed_cloud import reload_backend

        body = request.get_json(silent=True) or {}
        from basebuddy.modules import config as cfg

        url = (body.get("BASEBUDDY_CLOUD_API_URL") or cfg.BASEBUDDY_CLOUD_API_URL or "").strip()
        key = (body.get("BASEBUDDY_CLOUD_API_KEY") or "").strip() or cfg.BASEBUDDY_CLOUD_API_KEY
        backend = reload_backend(url, key)
        result = backend.test_connection()
        return jsonify({"ok": result.get("ok", False), "result": result})
    except ImportError:
        return jsonify(
            {
                "ok": False,
                "error": "Install premium package: pip install -e ./premium",
            }
        ), 501
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/test-remote", methods=["POST"])
def api_test_remote():
    try:
        from basebuddy.modules import config as cfg

        body = request.get_json(silent=True) or {}
        secret = (body.get("REMOTE_SECRET_KEY") or "").strip() or cfg.REMOTE_SECRET_KEY
        backend = S3ObjectStorage(
            enabled=True,
            provider=body.get("REMOTE_STORAGE_PROVIDER") or cfg.REMOTE_STORAGE_PROVIDER,
            bucket=(body.get("REMOTE_BUCKET") or cfg.REMOTE_BUCKET or "").strip(),
            access_key=(body.get("REMOTE_ACCESS_KEY") or cfg.REMOTE_ACCESS_KEY or "").strip(),
            secret_key=secret,
            region=(body.get("REMOTE_REGION") or cfg.REMOTE_REGION or "auto").strip(),
            endpoint_url=(body.get("REMOTE_ENDPOINT") or cfg.REMOTE_ENDPOINT or "").strip(),
            prefix=(body.get("REMOTE_PREFIX") or cfg.REMOTE_PREFIX or "").strip(),
        )
        return jsonify({"ok": True, "result": backend.test_connection()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/run-backup", methods=["POST"])
def api_run_backup():
    try:
        bm = _state().backup_manager
        if not bm:
            return jsonify({"ok": False, "error": "Backup manager not available"}), 500
        bm.perform_backup()
        return jsonify({"ok": True, "message": "Backup run finished (see logs for details)"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/run-archive", methods=["POST"])
def api_run_archive():
    try:
        ar = _state().archive_service
        if not ar:
            return jsonify({"ok": False, "error": "Archive service not available"}), 500
        result = ar.perform_archive()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/run-retention", methods=["POST"])
def api_run_retention():
    try:
        rs = _state().retention_service
        if not rs:
            return jsonify({"ok": False, "error": "Retention service not available"}), 500
        result = rs.perform_retention_pass()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/clear-camera-detections", methods=["POST"])
def api_clear_camera_detections():
    """Delete all detection events + image files for one camera."""
    try:
        body = request.get_json(silent=True) or {}
        if not body.get("confirm"):
            return jsonify({"ok": False, "error": "Set confirm=true to proceed"}), 400
        if "camera_id" not in body:
            return jsonify({"ok": False, "error": "camera_id required"}), 400
        try:
            camera_id = int(body["camera_id"])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "camera_id must be an integer"}), 400
        if camera_id < 0:
            return jsonify({"ok": False, "error": "camera_id must be >= 0"}), 400

        db = _state().analytics_db
        if not db:
            return jsonify({"ok": False, "error": "Database not available"}), 500

        result = db.delete_detections_for_camera(camera_id)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@storage_policy_bp.route("/api/storage-policy/drives", methods=["GET"])
def api_drives():
    try:
        bm = _state().backup_manager
        if not bm:
            return jsonify({"ok": False, "error": "Backup manager not available"}), 500
        return jsonify({"ok": True, "data": bm.find_external_drives()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
