"""
Unified notification entry — applies rules then dispatches to channels.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from basebuddy.core.services.media_paths import best_snapshot_path, url_to_filesystem
from basebuddy.core.services.notification_rules import channels_for_event
from basebuddy.core.services.notify_dispatcher import get_notify_dispatcher

logger = logging.getLogger(__name__)


def notify_detection(
    event_phase: str,
    camera_id: int,
    class_name: str,
    confidence: float,
    message: str,
    *,
    thumbnail_path: Optional[str] = None,
    full_image_path: Optional[str] = None,
    clip_path: Optional[str] = None,
    snapshot_url: Optional[str] = None,
    clip_url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send notification if rules allow. event_phase: start | end | region.
    Returns True if anything was sent.
    """
    from basebuddy.modules.config import env_live
    if str(env_live("NOTIFY_ENABLED", "true")).lower() != "true":
        return False

    plan = channels_for_event(camera_id, class_name, confidence, event_phase)
    channels = plan.get("channels") or set()
    if not channels:
        return False

    snap_fs, snap_web = best_snapshot_path(thumbnail_path, full_image_path)
    if snapshot_url and not snap_web:
        snap_web = snapshot_url
    clip_fs = url_to_filesystem(clip_path) if clip_path else None
    clip_web = clip_url or clip_path

    payload = {
        "camera_id": camera_id,
        "class_name": class_name,
        "confidence": confidence,
        "phase": event_phase,
        **(extra or {}),
    }

    get_notify_dispatcher().send(
        channels,
        message,
        snapshot_fs=snap_fs if plan.get("include_snapshot", True) else None,
        snapshot_url=snap_web,
        clip_fs=clip_fs if plan.get("include_clip") else None,
        clip_url=clip_web,
        payload=payload,
        include_snapshot=plan.get("include_snapshot", True),
        include_clip=plan.get("include_clip", False),
    )

    return True


def send_test_notification(channel: str = "telegram") -> Dict[str, Any]:
    """Send a test alert using configured credentials."""
    from basebuddy.modules import config as cfg

    if not str(cfg.env_live("NOTIFY_ENABLED", "true")).lower() == "true":
        return {"ok": False, "error": "Notifications are disabled (NOTIFY_ENABLED=false)"}

    configured = {
        "telegram": bool(cfg.env_live("TELEGRAM_BOT_TOKEN") and cfg.env_live("TELEGRAM_CHAT_ID")),
        "email": bool(cfg.env_live("SMTP_HOST") and cfg.env_live("SMTP_TO")),
        "pushover": bool(cfg.env_live("PUSHOVER_USER_KEY") and cfg.env_live("PUSHOVER_API_TOKEN")),
        "sms": bool(
            cfg.env_live("TWILIO_ACCOUNT_SID")
            and cfg.env_live("TWILIO_AUTH_TOKEN")
            and cfg.env_live("TWILIO_FROM_NUMBER")
            and cfg.env_live("TWILIO_TO_NUMBER")
        ),
        "webhook": bool(cfg.env_live("NOTIFY_WEBHOOK_URL")),
    }

    if channel != "all":
        if channel not in configured:
            return {"ok": False, "error": f"Unknown channel: {channel}"}
        if not configured[channel]:
            return {"ok": False, "error": f"{channel} is not configured — fill in credentials and save first"}
        ch = {channel}
    else:
        ch = {k for k, ok in configured.items() if ok}
        if not ch:
            return {"ok": False, "error": "No notification channels are configured"}

    import os
    from basebuddy.modules.config import MEDIA_BASE_DIR

    test_img = None
    base = MEDIA_BASE_DIR or ""
    if base and os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".jpg"):
                    test_img = os.path.join(root, f)
                    break
            if test_img:
                break

    msg = "BaseBuddy test notification — your alerts are configured correctly."
    get_notify_dispatcher().send(
        ch,
        msg,
        snapshot_fs=test_img,
        include_snapshot=bool(test_img),
        payload={"test": True},
    )
    return {"ok": True, "channels": list(ch), "had_image": bool(test_img)}
