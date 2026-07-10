"""
MQTT event publisher (Frigate-compatible topic shape).
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_client = None
_lock = threading.Lock()


def _cfg():
    from basebuddy.modules import config
    return config


def _ensure_client():
    global _client
    cfg = _cfg()
    if not getattr(cfg, "MQTT_ENABLED", False):
        return None
    with _lock:
        if _client is not None:
            return _client
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.warning("paho-mqtt not installed — pip install paho-mqtt")
            return None
        host = cfg.MQTT_HOST or "127.0.0.1"
        port = int(cfg.MQTT_PORT or 1883)
        client = mqtt.Client(client_id=cfg.MQTT_CLIENT_ID or "basebuddy")
        user = getattr(cfg, "MQTT_USERNAME", "") or ""
        password = getattr(cfg, "MQTT_PASSWORD", "") or ""
        if user:
            client.username_pw_set(user, password or None)
        try:
            client.connect(host, port, keepalive=60)
            client.loop_start()
            _client = client
            logger.info("MQTT connected to %s:%s", host, port)
        except Exception as exc:
            logger.warning("MQTT connect failed: %s", exc)
            return None
        return _client


def publish_event(camera_id: int, class_name: str, event_type: str, payload: Dict[str, Any]) -> None:
    """Publish to basebuddy/<camera>/<class>/<event_type> and summary topic."""
    cfg = _cfg()
    if not getattr(cfg, "MQTT_ENABLED", False):
        return
    client = _ensure_client()
    if client is None:
        return
    prefix = (cfg.MQTT_TOPIC_PREFIX or "basebuddy").strip("/")
    cam = f"camera_{camera_id + 1}"
    label = class_name.replace(" ", "_").lower()
    topic = f"{prefix}/{cam}/{label}/{event_type}"
    summary = f"{prefix}/{cam}/{label}"
    body = json.dumps(payload, default=str)
    try:
        with _lock:
            client.publish(topic, body, qos=0, retain=event_type == "end")
            if event_type == "new":
                client.publish(summary, body, qos=0, retain=True)
    except Exception as exc:
        logger.warning("MQTT publish failed: %s", exc)


def publish_raw(topic: str, payload: str, *, qos: int = 0) -> bool:
    """Publish arbitrary payload to a topic (plant actions, automations)."""
    cfg = _cfg()
    if not getattr(cfg, "MQTT_ENABLED", False):
        return False
    client = _ensure_client()
    if client is None:
        return False
    try:
        with _lock:
            client.publish(topic, payload, qos=qos)
        return True
    except Exception as exc:
        logger.warning("MQTT publish failed: %s", exc)
        return False


def disconnect_mqtt() -> None:
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.loop_stop()
                _client.disconnect()
            except Exception:
                pass
            _client = None
