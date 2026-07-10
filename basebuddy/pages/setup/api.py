"""Setup wizard API."""
from flask import jsonify, request

from basebuddy.core.config_persist import upsert_config_exports
from basebuddy.core.paths import get_repo_root
from basebuddy.core.services.setup_status import get_setup_status
from basebuddy.pages.setup import setup_bp


@setup_bp.route("/api/setup/status", methods=["GET"])
def api_setup_status():
    try:
        status = get_setup_status()
        from basebuddy.modules import config
        status["values"] = {
            "cam1": config.env_live("CAM1", "") or "",
            "cam2": config.env_live("CAM2", "") or "",
            "detection_enabled": str(config.env_live("DETECTION_ENABLED", "true")).lower() == "true",
            "notify_enabled": str(config.env_live("NOTIFY_ENABLED", "true")).lower() == "true",
            "telegram_token_set": bool(config.env_live("TELEGRAM_BOT_TOKEN", "")),
            "telegram_chat_id": config.env_live("TELEGRAM_CHAT_ID", "") or "",
            "notify_fallback": str(config.env_live("NOTIFY_FALLBACK_GLOBAL", "true")).lower() == "true",
        }
        return jsonify({"ok": True, **status})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/api/setup/cameras", methods=["POST"])
def api_setup_cameras():
    try:
        body = request.get_json(silent=True) or {}
        updates = {}
        for i in (1, 2):
            key = f"cam{i}"
            if key in body:
                updates[f"CAM{i}"] = str(body[key] or "").strip()
        if "detection_enabled" in body:
            updates["DETECTION_ENABLED"] = "true" if body["detection_enabled"] else "false"
        if "notify_enabled" in body:
            updates["NOTIFY_ENABLED"] = "true" if body["notify_enabled"] else "false"
        if not updates:
            return jsonify({"ok": False, "error": "No fields to save"}), 400
        upsert_config_exports(get_repo_root(), updates)
        _reload_config()
        return jsonify({"ok": True, "status": get_setup_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/api/setup/telegram", methods=["POST"])
def api_setup_telegram():
    try:
        body = request.get_json(silent=True) or {}
        updates = {"NOTIFY_ENABLED": "true"}
        token = str(body.get("bot_token") or "").strip()
        chat_id = str(body.get("chat_id") or "").strip()
        if token:
            updates["TELEGRAM_BOT_TOKEN"] = token
        if chat_id:
            updates["TELEGRAM_CHAT_ID"] = chat_id
        if not token and not chat_id:
            return jsonify({"ok": False, "error": "Bot token and chat ID required"}), 400
        upsert_config_exports(get_repo_root(), updates)
        _reload_config()
        return jsonify({"ok": True, "status": get_setup_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/api/setup/telegram/test", methods=["POST"])
def api_setup_telegram_test():
    try:
        from basebuddy.core.services.notification_service import send_test_notification
        result = send_test_notification("telegram")
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/api/setup/alerts", methods=["POST"])
def api_setup_alerts():
    """Create a default person → Telegram rule, or enable global fallback."""
    try:
        body = request.get_json(silent=True) or {}
        use_fallback = body.get("use_fallback", True)
        create_rule = body.get("create_rule", True)

        updates = {
            "NOTIFY_ENABLED": "true",
            "NOTIFY_FALLBACK_GLOBAL": "true" if use_fallback else "false",
        }
        upsert_config_exports(get_repo_root(), updates)
        _reload_config()

        if create_rule:
            from basebuddy.core.services.notification_rules import save_rule
            save_rule({
                "enabled": True,
                "camera_id": None,
                "class_name": "person",
                "notify_on": "start",
                "min_confidence": 0.35,
                "cooldown_s": 60,
                "channels": ["telegram"],
                "include_snapshot": True,
                "include_clip": False,
                "label": "Setup wizard default",
            })

        return jsonify({"ok": True, "status": get_setup_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/api/setup/complete", methods=["POST"])
def api_setup_complete():
    try:
        upsert_config_exports(get_repo_root(), {"ONBOARDING_COMPLETE": "true"})
        _reload_config()
        return jsonify({"ok": True, "status": get_setup_status()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _reload_config() -> None:
    from basebuddy.modules.config import load_config_file
    load_config_file()
