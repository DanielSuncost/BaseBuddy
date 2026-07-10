"""Capture frames, segmentation metrics, vision analysis, and actions."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from basebuddy.plugins.plant_health.metrics import extract_metrics


def capture_frame(camera_id: int, max_width: int = 1280) -> Tuple[Optional[np.ndarray], Optional[bytes], Optional[str]]:
    import basebuddy.modules.state as shared_state
    from datetime import datetime
    import os
    from basebuddy.modules.config import MEDIA_BASE_DIR
    from basebuddy.core.paths import abs_data_path

    grabber = shared_state.grabbers.get(camera_id)
    if grabber is None:
        return None, None, None
    frame, _ts = grabber.get_latest_frame()
    if frame is None:
        return None, None, None
    if max_width and frame.shape[1] > max_width:
        scale = max_width / float(frame.shape[1])
        frame = cv2.resize(
            frame,
            (max_width, max(1, int(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        return frame, None, None

    day = datetime.now().strftime("%Y-%m-%d")
    rel_dir = os.path.join("plant_health", f"cam{camera_id + 1}", day)
    out_dir = os.path.join(abs_data_path(MEDIA_BASE_DIR or "media"), rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    fname = f"plant_{int(time.time())}.jpg"
    out_path = os.path.join(out_dir, fname)
    with open(out_path, "wb") as fh:
        fh.write(buf.tobytes())
    web_path = f"/media/{rel_dir.replace(os.sep, '/')}/{fname}"
    return frame, buf.tobytes(), web_path


def run_monitor_cycle(monitor_id: str, trigger: str = "manual") -> Dict[str, Any]:
    from basebuddy.core.premium_hooks import get_plant_health_analyzer
    from basebuddy.plugins.plant_health.actions import run_actions
    from basebuddy.plugins.plant_health.config import get_monitor
    from basebuddy.plugins.plant_health.db import save_analysis, save_color_sample

    monitor = get_monitor(monitor_id)
    if not monitor:
        return {"ok": False, "error": "Monitor not found"}

    camera_id = int(monitor.get("camera_id", 0))
    frame, image_bytes, image_path = capture_frame(camera_id)
    if frame is None or not image_bytes:
        return {"ok": False, "error": "No frame from camera — is it running on Camera Wall?"}

    color_metrics = extract_metrics(frame, monitor)
    color_id = None
    if color_metrics:
        color_id = save_color_sample(monitor_id, camera_id, color_metrics, image_path=image_path)

    vision_result = None
    analyzer = get_plant_health_analyzer()
    if analyzer.is_configured():
        vision_result = analyzer.analyze(
            image_bytes,
            species_hint=monitor.get("species_hint") or "",
            monitor=monitor,
        )
        save_analysis(
            monitor_id,
            camera_id,
            vision_result,
            image_path=image_path,
            analyzer=getattr(analyzer, "name", "oss"),
            error=vision_result.get("error"),
        )

    action_results = run_actions(monitor, "on_sample", {
        "trigger": trigger,
        "health_score": str((vision_result or {}).get("health_score") or ""),
        "image_path": image_path or "",
        "greenness": str((color_metrics or {}).get("greenness") or ""),
    })

    recs = (vision_result or {}).get("recommendations") or []
    water_hint = any("water" in str(r).lower() for r in recs)
    if water_hint:
        run_actions(monitor, "on_water_recommendation", {"trigger": trigger, "recommendations": recs})

    ok = bool(color_metrics) or bool(vision_result and vision_result.get("ok"))
    return {
        "ok": ok,
        "color_sample_id": color_id,
        "color_metrics": color_metrics,
        "result": vision_result,
        "image_path": image_path,
        "actions": action_results,
        "analyzer": getattr(analyzer, "name", "oss"),
        "premium_available": _premium_available(),
    }


def analyze_monitor(monitor_id: str) -> Dict[str, Any]:
    return run_monitor_cycle(monitor_id, trigger="manual")


def _premium_available() -> bool:
    from basebuddy.core.premium_hooks import plant_health_premium_available
    return plant_health_premium_available()
