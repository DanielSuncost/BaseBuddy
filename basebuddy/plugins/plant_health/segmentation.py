"""Live-camera SAM segmentation for plant monitors."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def camera_key(camera_id: int) -> str:
    return f"camera_{int(camera_id)}"


def prompt_config_path(camera_id: int) -> str:
    return os.path.join("sam_prompt_configs", f"{camera_key(camera_id)}_prompts.json")


def capture_frame_bgr(camera_id: int, max_width: int = 1280) -> Optional[np.ndarray]:
    import basebuddy.modules.state as shared_state

    grabber = shared_state.grabbers.get(int(camera_id))
    if grabber is None:
        return None
    frame, _ts = grabber.get_latest_frame()
    if frame is None:
        return None
    if max_width and frame.shape[1] > max_width:
        scale = max_width / float(frame.shape[1])
        frame = cv2.resize(
            frame,
            (max_width, max(1, int(frame.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return frame


def _save_reference_still(camera_id: int, frame: np.ndarray) -> str:
    """Save frame under stills/ for color-profile analysis."""
    from basebuddy.core.upload_safety import safe_basename

    cam = camera_key(camera_id)
    out_dir = os.path.join("stills", cam)
    os.makedirs(out_dir, exist_ok=True)
    fname = safe_basename(f"plant_ref_{int(time.time())}.jpg") or f"plant_ref_{int(time.time())}.jpg"
    path = os.path.join(out_dir, fname)
    cv2.imwrite(path, frame)
    return path


def preview_mask_png(camera_id: int, points: List, labels: List) -> Tuple[Optional[bytes], Optional[str]]:
    """Return PNG overlay bytes for live preview."""
    frame = capture_frame_bgr(camera_id)
    if frame is None:
        return None, "No frame from camera"

    if not points:
        return None, "Add at least one point"

    from basebuddy.routes.plant_tracking import get_sam_predictor

    predictor = get_sam_predictor()
    if predictor is None:
        return None, "SAM not available — install segment-anything model"

    predictor.set_image(frame)
    point_coords = np.array(points, dtype=np.float32)
    point_labels = np.array(labels, dtype=np.int32)
    masks, scores, _ = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=False,
    )
    mask = (masks[0] * 255).astype(np.uint8)
    overlay = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
    overlay[mask > 0] = [16, 185, 129, 160]

    ok, buf = cv2.imencode(".png", overlay)
    if not ok:
        return None, "Encode failed"
    return buf.tobytes(), None


def render_preview_with_points(
    camera_id: int,
    points: List,
    labels: List,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Composite base frame + mask + point markers as JPEG."""
    frame = capture_frame_bgr(camera_id)
    if frame is None:
        return None, "No frame from camera"

    display = frame.copy()
    if points:
        from basebuddy.routes.plant_tracking import get_sam_predictor

        predictor = get_sam_predictor()
        if predictor is None:
            return None, (
                "SAM unavailable — install segment-anything and place "
                "sam_vit_b_01ec64.pth in the project root (see models/README.md)"
            )
        predictor.set_image(frame)
        point_coords = np.array(points, dtype=np.float32)
        point_labels = np.array(labels, dtype=np.int32)
        masks, _, _ = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=False,
        )
        mask = masks[0].astype(bool)
        tint = display.copy()
        tint[mask] = (tint[mask] * 0.45 + np.array([30, 185, 130]) * 0.55).astype(np.uint8)
        display = tint

    for (x, y), lab in zip(points, labels):
        color = (40, 200, 80) if lab == 1 else (60, 60, 220)
        cv2.circle(display, (int(x), int(y)), 7, color, -1)
        cv2.circle(display, (int(x), int(y)), 8, (255, 255, 255), 2)

    ok, buf = cv2.imencode(".jpg", display, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return None, "Encode failed"
    return buf.tobytes(), None


def save_segmentation_pattern(
    monitor_id: str,
    camera_id: int,
    points: List,
    labels: List,
) -> Dict[str, Any]:
    from basebuddy.routes.plant_tracking import analyze_prompt_pattern
    from basebuddy.plugins.plant_health.config import get_monitor, update_monitor

    if not get_monitor(monitor_id):
        return {"ok": False, "error": "Monitor not found"}

    fg = [p for p, l in zip(points, labels) if l == 1]
    if not fg:
        return {"ok": False, "error": "Add at least one point on the plant (left-click)"}

    frame = capture_frame_bgr(camera_id)
    if frame is None:
        return {"ok": False, "error": "No frame from camera"}

    still_path = _save_reference_still(camera_id, frame)
    pattern = analyze_prompt_pattern(points, labels, frame.shape, still_path)
    if not pattern:
        return {"ok": False, "error": "Could not build pattern"}

    os.makedirs("sam_prompt_configs", exist_ok=True)
    cfg_path = prompt_config_path(camera_id)
    if os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    else:
        cfg = {"camera_id": camera_key(camera_id), "patterns": []}

    cfg.setdefault("patterns", []).append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "reference_image": still_path,
        "pattern": pattern,
    })
    pattern_id = len(cfg["patterns"]) - 1

    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)

    bbox = pattern.get("bbox") or {}
    roi = None
    if bbox:
        roi = {
            "x": bbox.get("min_x", 0),
            "y": bbox.get("min_y", 0),
            "w": max(0.01, float(bbox.get("max_x", 1)) - float(bbox.get("min_x", 0))),
            "h": max(0.01, float(bbox.get("max_y", 1)) - float(bbox.get("min_y", 0))),
        }

    update_monitor(monitor_id, {
        "segmentation": {
            "mode": "color_profile",
            "pattern_id": pattern_id,
            "pattern_camera_id": int(camera_id),
        },
        "roi": roi,
    })

    return {
        "ok": True,
        "pattern_id": pattern_id,
        "has_color_profile": "color_profile" in pattern,
        "coverage_hint": pattern.get("bbox"),
    }
