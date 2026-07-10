"""Per-slot state transitions and alert rules."""
from __future__ import annotations

import json
import urllib.request
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


def transition_slot(
    scene_id: str,
    slot: dict,
    new_state: str,
    confidence: float,
) -> Tuple[str, bool]:
    """
    Apply state machine. Returns (final_state, alert_fired).
    """
    from basebuddy.plugins.home_scenes.db import add_scene_event, get_slot_state, upsert_slot_state

    slot_id = slot.get("id", "slot")
    rules = slot.get("rules") or {}
    confirm = int(rules.get("empty_confirm_checks", 2))
    prev = get_slot_state(scene_id, slot_id)
    old_state = prev["state"] if prev else "unknown"
    consecutive_empty = prev["consecutive_empty"] if prev else 0

    if new_state == "empty":
        consecutive_empty += 1
    else:
        consecutive_empty = 0

    final_state = new_state
    alert = False

    if new_state == "empty" and consecutive_empty >= confirm:
        final_state = "empty"
        if old_state != "empty":
            alert = True
    elif new_state == "present":
        final_state = "present"

    upsert_slot_state(scene_id, slot_id, final_state, confidence, consecutive_empty)

    if old_state != final_state:
        add_scene_event(scene_id, slot_id, "state_change", old_state, final_state)

    if alert and rules.get("notify", True):
        add_scene_event(scene_id, slot_id, "alert", old_state, final_state)
        webhook = rules.get("webhook_url")
        if webhook:
            _post_webhook(webhook, scene_id, slot, final_state)
            add_scene_event(scene_id, slot_id, "webhook_sent", old_state, final_state)

    return final_state, alert


def _post_webhook(url: str, scene_id: str, slot: dict, state: str) -> None:
    body = json.dumps({
        "scene_id": scene_id,
        "slot_id": slot.get("id"),
        "label": slot.get("label"),
        "state": state,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        logger.error(f"Scene webhook failed for {scene_id}/{slot.get('id')}: {exc}")
