"""Home scene configuration (pantry, fridge, shelf monitoring)."""
from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from typing import Any, Dict, List

from basebuddy.core.config_persist import upsert_config_exports


def _project_root() -> str:
    from basebuddy.core.paths import get_repo_root
    return get_repo_root()


def _default_config() -> dict:
    return {"scenes": []}


def load_scene_config() -> dict:
    from basebuddy.modules.config import DEF

    raw = DEF("SCENE_CONFIG", "")
    if not raw:
        config_path = os.path.join(_project_root(), "config.txt")
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:]
                    if line.startswith("SCENE_CONFIG="):
                        raw = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
    if not raw:
        return _default_config()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "scenes" in data:
            return data
    except json.JSONDecodeError:
        pass
    return _default_config()


def save_scene_config(config: dict) -> None:
    upsert_config_exports(_project_root(), {"SCENE_CONFIG": json.dumps(config, separators=(",", ":"))})
    os.environ["SCENE_CONFIG"] = json.dumps(config, separators=(",", ":"))


def list_scenes() -> List[dict]:
    return load_scene_config().get("scenes", [])


def get_scene(scene_id: str) -> dict | None:
    for scene in list_scenes():
        if scene.get("id") == scene_id:
            return scene
    return None


def create_scene(payload: dict) -> dict:
    config = load_scene_config()
    scene = {
        "id": payload.get("id") or f"scene-{uuid.uuid4().hex[:8]}",
        "scene_type": payload.get("scene_type", "pantry"),
        "camera_id": int(payload.get("camera_id", 0)),
        "name": payload.get("name", "Untitled scene"),
        "enabled": bool(payload.get("enabled", True)),
        "check_interval_s": int(payload.get("check_interval_s", 300)),
        "slots": payload.get("slots", []),
        "fridge_door_roi": payload.get("fridge_door_roi"),
    }
    config.setdefault("scenes", []).append(scene)
    save_scene_config(config)
    return scene


def update_scene(scene_id: str, payload: dict) -> dict | None:
    config = load_scene_config()
    for idx, scene in enumerate(config.get("scenes", [])):
        if scene.get("id") == scene_id:
            updated = deepcopy(scene)
            for key in ("name", "scene_type", "enabled", "check_interval_s", "slots", "fridge_door_roi", "camera_id"):
                if key in payload:
                    updated[key] = payload[key]
            config["scenes"][idx] = updated
            save_scene_config(config)
            return updated
    return None


def delete_scene(scene_id: str) -> bool:
    config = load_scene_config()
    scenes = config.get("scenes", [])
    new_scenes = [s for s in scenes if s.get("id") != scene_id]
    if len(new_scenes) == len(scenes):
        return False
    config["scenes"] = new_scenes
    save_scene_config(config)
    return True


def scenes_root() -> str:
    root = os.path.join(_project_root(), "scenes")
    os.makedirs(root, exist_ok=True)
    return root


def slot_baseline_path(scene_id: str, slot_id: str) -> str:
    return os.path.join(scenes_root(), scene_id, f"{slot_id}_baseline.jpg")
