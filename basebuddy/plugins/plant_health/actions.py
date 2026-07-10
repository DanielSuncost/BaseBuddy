"""
Generic plant-care actions — hook pumps, lights, or any HTTP/MQTT device.

Supported integration paths (OSS):
  webhook     — Any HTTP GET/POST (Shelly relay URL, IFTTT, Node-RED, custom scripts)
  mqtt        — Publish to a topic (Home Assistant, Tasmota, Shelly MQTT, Zigbee2MQTT)
  homeassistant — POST /api/services/<domain>/<service> (requires HA long-lived token)

Common hardware ecosystems (configure via webhook or MQTT):
  Shelly      — http://<ip>/relay/0?turn=on  or  shellyplugsg3-<id>/command/switch:0
  Tasmota     — cmnd/<device>/POWER ON
  ESPHome     — native API or HA integration via MQTT
  Home Assistant — switch.turn_on, script.turn_on, rest_command.*
  Kasa/Tapo   — usually via HA or vendor cloud webhooks

Premium: BaseBuddy Cloud can ship pre-built action templates per device.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

ACTION_TYPES = ("webhook", "mqtt", "homeassistant", "none")


def run_actions(monitor: dict, trigger: str, context: Dict[str, Any] | None = None) -> List[dict]:
    """Execute enabled actions matching *trigger* (on_sample, manual, on_water_recommendation)."""
    context = context or {}
    results = []
    for action in monitor.get("actions") or []:
        if not action.get("enabled"):
            continue
        if action.get("trigger", "manual") != trigger:
            continue
        try:
            results.append(_dispatch(action, monitor, context))
        except Exception as exc:
            logger.warning("Plant action failed: %s", exc)
            results.append({"ok": False, "action_id": action.get("id"), "error": str(exc)})
    return results


def _dispatch(action: dict, monitor: dict, context: dict) -> dict:
    atype = (action.get("type") or "webhook").lower()
    cfg = action.get("config") or {}
    aid = action.get("id") or action.get("label") or "action"

    if atype == "webhook":
        return _webhook(aid, cfg, monitor, context)
    if atype == "mqtt":
        return _mqtt(aid, cfg, monitor, context)
    if atype == "homeassistant":
        return _homeassistant(aid, cfg, monitor, context)
    return {"ok": False, "action_id": aid, "error": f"Unknown action type: {atype}"}


def _webhook(aid: str, cfg: dict, monitor: dict, context: dict) -> dict:
    url = (cfg.get("url") or "").strip()
    if not url:
        return {"ok": False, "action_id": aid, "error": "No URL configured"}
    method = (cfg.get("method") or "POST").upper()
    body = _render_template(cfg.get("body") or cfg.get("body_template") or "{}", monitor, context)
    headers = {"Content-Type": "application/json"}
    if cfg.get("headers") and isinstance(cfg["headers"], dict):
        headers.update(cfg["headers"])
    data = body.encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    urllib.request.urlopen(req, timeout=15)
    return {"ok": True, "action_id": aid, "type": "webhook"}


def _mqtt(aid: str, cfg: dict, monitor: dict, context: dict) -> dict:
    topic = (cfg.get("topic") or "").strip()
    if not topic:
        return {"ok": False, "action_id": aid, "error": "No MQTT topic"}
    payload = _render_template(cfg.get("payload") or '{"event":"plant_action"}', monitor, context)
    from basebuddy.core.services.mqtt_publisher import publish_raw
    if not publish_raw(topic, payload, qos=int(cfg.get("qos", 0))):
        return {"ok": False, "action_id": aid, "error": "MQTT publish failed (enable MQTT in Integrations)"}
    return {"ok": True, "action_id": aid, "type": "mqtt", "topic": topic}


def _homeassistant(aid: str, cfg: dict, monitor: dict, context: dict) -> dict:
    base = (cfg.get("url") or "").strip().rstrip("/")
    token = (cfg.get("token") or "").strip()
    domain = cfg.get("domain") or "switch"
    service = cfg.get("service") or "turn_on"
    entity = cfg.get("entity_id") or ""
    if not base or not token:
        return {"ok": False, "action_id": aid, "error": "HA url and token required"}
    url = f"{base}/api/services/{domain}/{service}"
    body = json.dumps({"entity_id": entity, **(cfg.get("data") or {})})
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=15)
    return {"ok": True, "action_id": aid, "type": "homeassistant"}


def _render_template(template: str, monitor: dict, context: dict) -> str:
    if not isinstance(template, str):
        template = json.dumps(template)
    subs = {
        "monitor_id": monitor.get("id", ""),
        "monitor_name": monitor.get("name", ""),
        "species": monitor.get("species_hint", ""),
        **{k: str(v) for k, v in context.items()},
    }
    out = template
    for k, v in subs.items():
        out = out.replace("{{" + k + "}}", v)
    return out
