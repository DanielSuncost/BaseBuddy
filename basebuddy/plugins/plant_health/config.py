"""Plant monitor configuration (JSON in config.txt)."""
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
    return {"monitors": []}


def load_plant_config() -> dict:
    from basebuddy.modules.config import DEF
    raw = DEF("PLANT_MONITOR_CONFIG", "")
    if not raw:
        return _default_config()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "monitors" in data:
            return data
    except json.JSONDecodeError:
        pass
    return _default_config()


def save_plant_config(config: dict) -> None:
    blob = json.dumps(config, separators=(",", ":"))
    upsert_config_exports(_project_root(), {"PLANT_MONITOR_CONFIG": blob})
    os.environ["PLANT_MONITOR_CONFIG"] = blob


def list_monitors() -> List[dict]:
    return load_plant_config().get("monitors", [])


def get_monitor(monitor_id: str) -> Optional[dict]:
    for m in list_monitors():
        if m.get("id") == monitor_id:
            return m
    return None


def create_monitor(payload: dict) -> dict:
    config = load_plant_config()
    monitor = {
        "id": payload.get("id") or f"plant-{uuid.uuid4().hex[:8]}",
        "name": payload.get("name") or "Plant monitor",
        "camera_id": int(payload.get("camera_id", 0)),
        "species_hint": (payload.get("species_hint") or "").strip(),
        "check_interval_s": int(payload.get("check_interval_s") or 3600),
        "enabled": bool(payload.get("enabled", True)),
        "roi": payload.get("roi"),
        "schedule": payload.get("schedule") or {
            "mode": "interval",
            "interval_s": int(payload.get("check_interval_s") or 3600),
            "enabled": True,
        },
        "segmentation": payload.get("segmentation") or {"mode": "auto"},
        "actions": payload.get("actions") or [],
    }
    config.setdefault("monitors", []).append(monitor)
    save_plant_config(config)
    return monitor


def update_monitor(monitor_id: str, payload: dict) -> Optional[dict]:
    config = load_plant_config()
    for i, m in enumerate(config.get("monitors", [])):
        if m.get("id") != monitor_id:
            continue
        updated = {**m, **{k: v for k, v in payload.items() if k != "id"}}
        config["monitors"][i] = updated
        save_plant_config(config)
        return updated
    return None


def delete_monitor(monitor_id: str) -> bool:
    config = load_plant_config()
    before = len(config.get("monitors", []))
    config["monitors"] = [m for m in config.get("monitors", []) if m.get("id") != monitor_id]
    if len(config["monitors"]) == before:
        return False
    save_plant_config(config)
    return True
