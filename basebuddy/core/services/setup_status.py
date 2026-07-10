"""First-run setup checklist — cameras, detection, Telegram, alert rules."""
from __future__ import annotations

from typing import Any, Dict, List


def _reload_config() -> None:
    from basebuddy.modules.config import load_config_file
    load_config_file()


def _camera_urls() -> List[str]:
    import os
    urls = []
    for i in range(1, 21):
        u = (os.environ.get(f"CAM{i}") or "").strip()
        if u:
            urls.append(u)
    return urls


def _alert_rules_count() -> int:
    try:
        import basebuddy.modules.state as st
        db = st.analytics_db
        if db is None:
            return 0
        return len(db.list_notification_rules(enabled_only=True))
    except Exception:
        return 0


def get_setup_status() -> Dict[str, Any]:
    _reload_config()
    from basebuddy.modules import config

    cameras = _camera_urls()
    has_camera = bool(cameras)
    has_detection = str(config.env_live("DETECTION_ENABLED", "true")).lower() == "true"
    has_telegram = bool(
        (config.env_live("TELEGRAM_BOT_TOKEN") or "")
        and (config.env_live("TELEGRAM_CHAT_ID") or "")
    )
    notify_enabled = str(config.env_live("NOTIFY_ENABLED", "true")).lower() == "true"
    fallback = str(config.env_live("NOTIFY_FALLBACK_GLOBAL", "true")).lower() == "true"
    rule_count = _alert_rules_count()
    has_alerts = rule_count > 0 or (has_telegram and fallback and notify_enabled)

    steps = [
        {
            "id": "camera",
            "label": "Add a camera stream",
            "done": has_camera,
            "required": True,
        },
        {
            "id": "detection",
            "label": "Enable AI detection",
            "done": has_detection,
            "required": True,
        },
        {
            "id": "telegram",
            "label": "Connect Telegram",
            "done": has_telegram,
            "required": True,
        },
        {
            "id": "alerts",
            "label": "Configure alert delivery",
            "done": has_alerts,
            "required": True,
        },
    ]
    done_count = sum(1 for s in steps if s["done"])
    onboarding_complete = str(config.env_live("ONBOARDING_COMPLETE", "false")).lower() == "true"
    essentials_ready = has_camera and has_telegram and notify_enabled

    return {
        "onboarding_complete": onboarding_complete,
        "show_banner": not onboarding_complete and not essentials_ready,
        "essentials_ready": essentials_ready,
        "steps": steps,
        "progress": int(100 * done_count / len(steps)) if steps else 0,
        "camera_count": len(cameras),
        "rule_count": rule_count,
        "notify_enabled": notify_enabled,
        "telegram_configured": has_telegram,
        "detection_enabled": has_detection,
    }
