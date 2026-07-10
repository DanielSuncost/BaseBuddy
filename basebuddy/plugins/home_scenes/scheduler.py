"""Background scheduler for home scene checks."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

import cv2

from basebuddy.plugins.home_scenes.config import get_scene, list_scenes, slot_baseline_path
from basebuddy.plugins.home_scenes.occupancy import compare_occupancy, detect_door_open, extract_roi
from basebuddy.plugins.home_scenes.state import transition_slot
import logging

logger = logging.getLogger(__name__)


class SceneScheduler:
    def __init__(self, interval_s: float = 30.0):
        self.interval_s = interval_s
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_check: Dict[str, float] = {}
        self._door_brightness: Dict[str, float] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scene-scheduler")
        self._thread.start()
        logger.info("Home scenes scheduler started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.run_due_checks()
            except Exception as exc:
                logger.error(f"Scene scheduler error: {exc}")
            time.sleep(self.interval_s)

    def run_due_checks(self) -> None:
        now = time.time()
        for scene in list_scenes():
            if not scene.get("enabled", True):
                continue
            scene_id = scene.get("id")
            interval = int(scene.get("check_interval_s", 300))
            last = self._last_check.get(scene_id, 0)
            if now - last < interval:
                continue
            self._last_check[scene_id] = now
            self.check_scene(scene_id)

    def check_scene(self, scene_id: str) -> dict:
        scene = get_scene(scene_id)
        if not scene:
            return {"ok": False, "error": "scene not found"}

        frame = self._capture_frame(int(scene.get("camera_id", 0)))
        if frame is None:
            return {"ok": False, "error": "no frame from camera"}

        door_open = False
        if scene.get("scene_type") == "fridge" and scene.get("fridge_door_roi"):
            ref = self._door_brightness.get(scene_id)
            door_open, brightness = detect_door_open(frame, scene["fridge_door_roi"], ref)
            if ref is None:
                self._door_brightness[scene_id] = brightness
            if door_open:
                return {"ok": True, "scene_id": scene_id, "door_open": True, "slots": []}

        results = []
        for slot in scene.get("slots", []):
            slot_id = slot.get("id", "slot")
            crop = extract_roi(frame, slot.get("roi", {}))
            baseline = slot.get("baseline_image") or slot_baseline_path(scene_id, slot_id)
            threshold = float(slot.get("occupancy_threshold", 25.0))
            state, confidence = compare_occupancy(crop, baseline, threshold=threshold)
            final_state, alert = transition_slot(scene_id, slot, state, confidence)
            results.append({
                "slot_id": slot_id,
                "label": slot.get("label"),
                "state": final_state,
                "confidence": confidence,
                "alert": alert,
            })

        return {"ok": True, "scene_id": scene_id, "door_open": door_open, "slots": results}

    @staticmethod
    def _capture_frame(camera_id: int):
        import basebuddy.modules.state as shared_state

        grabber = shared_state.grabbers.get(camera_id)
        if grabber is None:
            return None
        try:
            frame, _ts = grabber.get_latest_frame()
            return frame
        except Exception:
            return None

    def capture_baseline(self, scene_id: str, slot_id: str) -> str | None:
        scene = get_scene(scene_id)
        if not scene:
            return None
        frame = self._capture_frame(int(scene.get("camera_id", 0)))
        if frame is None:
            return None
        slot = next((s for s in scene.get("slots", []) if s.get("id") == slot_id), None)
        if not slot:
            return None
        crop = extract_roi(frame, slot.get("roi", {}))
        if crop.size == 0:
            return None
        path = slot_baseline_path(scene_id, slot_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cv2.imwrite(path, crop)
        from basebuddy.plugins.home_scenes.config import load_scene_config, save_scene_config

        config = load_scene_config()
        for s in config.get("scenes", []):
            if s.get("id") == scene_id:
                for sl in s.get("slots", []):
                    if sl.get("id") == slot_id:
                        sl["baseline_image"] = path
        save_scene_config(config)
        return path


_scheduler: SceneScheduler | None = None


def get_scene_scheduler() -> SceneScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = SceneScheduler()
    return _scheduler
