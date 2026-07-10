"""Plant blogger channel configuration (JSON in config.txt)."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

from basebuddy.core.config_persist import upsert_config_exports


def _project_root() -> str:
    from basebuddy.core.paths import get_repo_root
    return get_repo_root()


def _default_config() -> dict:
    return {"channels": []}


def load_blogger_config() -> dict:
    from basebuddy.modules.config import DEF
    raw = DEF("PLANT_BLOGGER_CONFIG", "")
    if not raw:
        return _default_config()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "channels" in data:
            return data
    except json.JSONDecodeError:
        pass
    return _default_config()


def save_blogger_config(config: dict) -> None:
    blob = json.dumps(config, separators=(",", ":"))
    upsert_config_exports(_project_root(), {"PLANT_BLOGGER_CONFIG": blob})
    os.environ["PLANT_BLOGGER_CONFIG"] = blob


def list_channels() -> List[dict]:
    return load_blogger_config().get("channels", [])


def get_channel(channel_id: str) -> Optional[dict]:
    for ch in list_channels():
        if ch.get("id") == channel_id:
            return ch
    return None


def create_channel(payload: dict) -> dict:
    config = load_blogger_config()
    channel = _normalize_channel(payload)
    channel["id"] = channel.get("id") or f"blog-{uuid.uuid4().hex[:8]}"
    config.setdefault("channels", []).append(channel)
    save_blogger_config(config)
    return channel


def update_channel(channel_id: str, payload: dict) -> Optional[dict]:
    config = load_blogger_config()
    for i, ch in enumerate(config.get("channels", [])):
        if ch.get("id") != channel_id:
            continue
        merged = {**ch, **_normalize_channel({**ch, **payload}), "id": channel_id}
        config["channels"][i] = merged
        save_blogger_config(config)
        return merged
    return None


def delete_channel(channel_id: str) -> bool:
    config = load_blogger_config()
    before = len(config.get("channels", []))
    config["channels"] = [c for c in config.get("channels", []) if c.get("id") != channel_id]
    if len(config["channels"]) == before:
        return False
    save_blogger_config(config)
    return True


def _normalize_channel(payload: dict) -> dict:
    schedule = payload.get("schedule") or {}
    mode = schedule.get("mode") or "interval"
    if mode == "times":
        schedule = {
            "mode": "times",
            "times": schedule.get("times") or ["09:00"],
            "days": schedule.get("days"),
            "enabled": schedule.get("enabled", True),
        }
    else:
        schedule = {
            "mode": "interval",
            "interval_s": int(schedule.get("interval_s") or payload.get("interval_s") or 86400),
            "enabled": schedule.get("enabled", True),
        }

    content = payload.get("content") or {}
    normalized_content = {
        "include_image": bool(content.get("include_image", True)),
        "include_health_score": bool(content.get("include_health_score", True)),
        "include_summary": bool(content.get("include_summary", True)),
        "include_species": bool(content.get("include_species", True)),
        "include_greenness": bool(content.get("include_greenness", False)),
        "include_coverage": bool(content.get("include_coverage", False)),
        "include_recommendations": bool(content.get("include_recommendations", False)),
        "run_vision_before_post": bool(content.get("run_vision_before_post", True)),
        "custom_intro": (content.get("custom_intro") or "").strip(),
        "custom_outro": (content.get("custom_outro") or "").strip(),
        "hashtags": (content.get("hashtags") or "").strip(),
        "title_template": (content.get("title_template") or "{{monitor_name}} update").strip(),
        "caption_template": (content.get("caption_template") or "").strip(),
    }

    dest = payload.get("destination") or {}
    return {
        "name": (payload.get("name") or "Plant blog").strip(),
        "monitor_id": (payload.get("monitor_id") or "").strip(),
        "enabled": bool(payload.get("enabled", True)),
        "schedule": schedule,
        "destination": {
            "type": (dest.get("type") or "webhook").lower(),
            "config": dest.get("config") or {},
        },
        "content": normalized_content,
    }
