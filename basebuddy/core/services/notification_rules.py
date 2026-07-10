"""
Per-camera / per-class notification rules with cooldowns.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CHANNELS = ["telegram", "email", "pushover", "sms", "webhook"]

_last_fired: Dict[tuple, float] = {}
_lock = threading.Lock()


def _db():
    import basebuddy.modules.state as st
    return st.analytics_db


def list_rules(camera_id: Optional[int] = None) -> List[Dict[str, Any]]:
    return _db().list_notification_rules(camera_id=camera_id)


def save_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    return _db().upsert_notification_rule(rule)


def delete_rule(rule_id: int) -> bool:
    return _db().delete_notification_rule(rule_id)


def match_rules(
    camera_id: int,
    class_name: str,
    confidence: float,
    event_phase: str,
) -> List[Dict[str, Any]]:
    """Return enabled rules matching this detection. event_phase: start | end | region."""
    rules = _db().list_notification_rules(enabled_only=True)
    matched = []
    phase = event_phase if event_phase in ("start", "end", "region") else "start"
    for rule in rules:
        rcam = rule.get("camera_id")
        if rcam is not None and int(rcam) != int(camera_id):
            continue
        rclass = (rule.get("class_name") or "").strip().lower()
        if rclass and rclass != "*" and rclass != class_name.lower():
            continue
        min_conf = float(rule.get("min_confidence") or 0)
        if confidence < min_conf:
            continue
        notify_on = (rule.get("notify_on") or "start").lower()
        if phase == "region":
            if notify_on not in ("region", "both"):
                continue
        elif phase == "start" and notify_on not in ("start", "both"):
            continue
        elif phase == "end" and notify_on not in ("end", "both"):
            continue
        matched.append(rule)
    return matched


def cooldown_ok(rule_id: int, camera_id: int, class_name: str, cooldown_s: float) -> bool:
    key = (rule_id, camera_id, class_name.lower())
    now = time.time()
    with _lock:
        if now - _last_fired.get(key, 0.0) < cooldown_s:
            return False
        _last_fired[key] = now
    return True


def channels_for_event(
    camera_id: int,
    class_name: str,
    confidence: float,
    event_phase: str,
) -> Dict[str, Any]:
    """
    Resolve which channels to use and options (include_clip, etc.).
    Returns { channels: set, include_snapshot: bool, include_clip: bool, rules_matched: int }
    """
    from basebuddy.modules import config as cfg

    rules = match_rules(camera_id, class_name, confidence, event_phase)
    channels: set = set()
    include_snapshot = False
    include_clip = False

    for rule in rules:
        rid = int(rule.get("id") or 0)
        cd = float(rule.get("cooldown_s") or 60)
        if not cooldown_ok(rid, camera_id, class_name, cd):
            continue
        for ch in rule.get("channels") or []:
            channels.add(ch)
        if rule.get("include_snapshot", True):
            include_snapshot = True
        if rule.get("include_clip"):
            include_clip = True

    fallback = str(cfg.env_live("NOTIFY_FALLBACK_GLOBAL", "true")).lower() == "true"
    if not channels and fallback:
        if str(cfg.env_live("NOTIFY_ENABLED", "true")).lower() == "true":
            if cfg.env_live("TELEGRAM_BOT_TOKEN") and cfg.env_live("TELEGRAM_CHAT_ID"):
                channels.add("telegram")
            if cfg.env_live("SMTP_HOST"):
                channels.add("email")
            if cfg.env_live("PUSHOVER_USER_KEY") and cfg.env_live("PUSHOVER_API_TOKEN"):
                channels.add("pushover")
            if cfg.env_live("TWILIO_ACCOUNT_SID") and cfg.env_live("TWILIO_AUTH_TOKEN"):
                channels.add("sms")
            if cfg.env_live("NOTIFY_WEBHOOK_URL"):
                channels.add("webhook")
            include_snapshot = True
            if event_phase == "end":
                include_clip = str(cfg.env_live("NOTIFY_INCLUDE_CLIP_DEFAULT", "true")).lower() == "true"

    return {
        "channels": channels,
        "include_snapshot": include_snapshot,
        "include_clip": include_clip and event_phase == "end",
        "rules_matched": len(rules),
    }


def parse_channels(raw) -> List[str]:
    if isinstance(raw, list):
        return [str(c).strip().lower() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parse_channels(parsed)
        except json.JSONDecodeError:
            pass
        return [c.strip().lower() for c in raw.split(",") if c.strip()]
    return []
