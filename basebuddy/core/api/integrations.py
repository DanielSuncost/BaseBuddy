"""Integrations settings API (MQTT, notifications, recording modes)."""
from flask import Blueprint, jsonify, request

from basebuddy.core.config_persist import upsert_config_exports
from basebuddy.core.paths import get_repo_root

integrations_api = Blueprint("integrations_api", __name__)

_KEYS = [
    "MQTT_ENABLED", "MQTT_HOST", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD",
    "MQTT_TOPIC_PREFIX", "MQTT_CLIENT_ID",
    "NOTIFY_ENABLED", "NOTIFY_WEBHOOK_URL", "NOTIFY_PUBLIC_BASE_URL",
    "NOTIFY_FALLBACK_GLOBAL", "NOTIFY_INCLUDE_CLIP_DEFAULT",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "PUSHOVER_USER_KEY", "PUSHOVER_API_TOKEN",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_TO", "SMTP_USE_TLS",
    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "TWILIO_TO_NUMBER",
    "RECORDING_MODE", "EVENT_CLIP_PRE_S", "EVENT_CLIP_POST_S",
    "FFMPEG_HWACCEL", "INFERENCE_BACKEND",
    "MULTIPROC_DETECTION", "LPR_ENABLED", "LPR_CLASSES",
]

_BOOL_KEYS = {
    "MQTT_ENABLED", "NOTIFY_ENABLED", "NOTIFY_FALLBACK_GLOBAL", "NOTIFY_INCLUDE_CLIP_DEFAULT",
    "MULTIPROC_DETECTION", "LPR_ENABLED", "SMTP_USE_TLS",
}


def _snapshot():
    from basebuddy.modules import config
    import basebuddy.modules.config as cfg_mod
    cfg_mod.load_config_file()
    out = {}
    for k in _KEYS:
        v = getattr(config, k, None)
        if isinstance(v, bool):
            out[k] = v
        elif v is None:
            out[k] = ""
        else:
            out[k] = v
    out["TELEGRAM_BOT_TOKEN_SET"] = bool(getattr(config, "TELEGRAM_BOT_TOKEN", ""))
    out["MQTT_PASSWORD_SET"] = bool(getattr(config, "MQTT_PASSWORD", ""))
    out["SMTP_PASSWORD_SET"] = bool(getattr(config, "SMTP_PASSWORD", ""))
    return out


@integrations_api.route("/api/integrations/settings", methods=["GET"])
def get_settings():
    try:
        return jsonify({"ok": True, "data": _snapshot()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/settings", methods=["POST"])
def save_settings():
    try:
        body = request.get_json(silent=True) or {}
        updates = {}
        for k in _KEYS:
            if k not in body:
                continue
            v = body[k]
            if k in _BOOL_KEYS:
                updates[k] = "true" if v in (True, "true", "1", 1, "on") else "false"
            else:
                updates[k] = str(v).strip() if v is not None else ""
        if updates:
            upsert_config_exports(get_repo_root(), updates)
            from basebuddy.modules.config import load_config_file
            load_config_file()
            if "MQTT_ENABLED" in updates:
                from basebuddy.core.services.mqtt_publisher import disconnect_mqtt
                disconnect_mqtt()
        return jsonify({"ok": True, "data": _snapshot()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/test-mqtt", methods=["POST"])
def test_mqtt():
    try:
        from basebuddy.core.services.mqtt_publisher import publish_event
        publish_event(0, "test", "new", {"message": "BaseBuddy MQTT test", "test": True})
        return jsonify({"ok": True, "message": "Test message published"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/test-notify", methods=["POST"])
def test_notify():
    try:
        body = request.get_json(silent=True) or {}
        channel = (body.get("channel") or "telegram").lower()
        from basebuddy.core.services.notification_service import send_test_notification
        result = send_test_notification(channel)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/notification-rules", methods=["GET"])
def list_notification_rules():
    try:
        from basebuddy.core.services.notification_rules import list_rules
        return jsonify({"ok": True, "rules": list_rules()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/notification-rules", methods=["POST"])
def save_notification_rule():
    try:
        from basebuddy.core.services.notification_rules import save_rule
        body = request.get_json(silent=True) or {}
        rule = save_rule(body)
        return jsonify({"ok": True, "rule": rule})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/notification-rules/<int:rule_id>", methods=["DELETE"])
def delete_notification_rule(rule_id):
    try:
        from basebuddy.core.services.notification_rules import delete_rule
        ok = delete_rule(rule_id)
        return jsonify({"ok": ok})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@integrations_api.route("/api/integrations/cameras", methods=["GET"])
def list_cameras_for_rules():
    try:
        import basebuddy.modules.state as st
        cams = []
        try:
            from basebuddy.modules.database import AnalyticsDB
            db = st.analytics_db or AnalyticsDB()
            with db._connect() as conn:
                cur = conn.execute(
                    "SELECT id, name FROM cameras WHERE enabled = 1 ORDER BY id"
                )
                for row in cur.fetchall():
                    cams.append({"id": row[0], "name": row[1] or f"Camera {row[0] + 1}"})
        except Exception:
            pass
        if not cams:
            for cid in sorted((st.grabbers or {}).keys()):
                cams.append({"id": cid, "name": f"Camera {cid + 1}"})
        return jsonify({"ok": True, "cameras": cams})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
