"""
Per-camera registration region config for 3D reconstruction.

Burned-in text (timestamps, camera-name banners, logos) produces identical
pixels across views and gets matched as false "shared" registration points.
This module stores per-camera constraints and builds the binary masks
(255 = usable for registration, 0 = ignored) that engines and the feature
matcher consume:

- exclude boxes: always removed from registration (e.g. OSD text areas)
- include boxes: if any exist, ONLY those areas are used
- use_seg_mask:  additionally intersect with the camera's saved SAM /
                 plant-segmentation mask when one is available

Boxes are stored in normalized [0-1] coordinates so they survive
resolution changes between the editor preview and full-size frames.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

_REGIONS_DIRNAME = "regions"


def _regions_dir() -> str:
    from basebuddy.core.paths import get_repo_root

    return os.path.join(get_repo_root(), "multiview_data", _REGIONS_DIRNAME)


def _regions_path(camera_id: int) -> str:
    return os.path.join(_regions_dir(), f"camera_{camera_id}.json")


def _sanitize_boxes(boxes) -> list:
    """Validate/clamp a list of normalized [x1, y1, x2, y2] boxes."""
    out = []
    if not isinstance(boxes, list):
        return out
    for box in boxes:
        try:
            x1, y1, x2, y2 = (float(v) for v in box[:4])
        except (TypeError, ValueError, IndexError):
            continue
        x1, x2 = sorted((min(max(x1, 0.0), 1.0), min(max(x2, 0.0), 1.0)))
        y1, y2 = sorted((min(max(y1, 0.0), 1.0), min(max(y2, 0.0), 1.0)))
        if (x2 - x1) < 0.005 or (y2 - y1) < 0.005:  # degenerate box
            continue
        out.append([round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)])
    return out


def load_regions(camera_id: int) -> dict:
    """Load region config for a camera; returns defaults when unset."""
    path = _regions_path(camera_id)
    config = {"exclude": [], "include": [], "use_seg_mask": False}
    try:
        with open(path, "r") as fh:
            raw = json.load(fh)
        config["exclude"] = _sanitize_boxes(raw.get("exclude"))
        config["include"] = _sanitize_boxes(raw.get("include"))
        config["use_seg_mask"] = bool(raw.get("use_seg_mask"))
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"Bad regions file for camera {camera_id}: {exc}")
    return config


def save_regions(camera_id: int, config: dict) -> dict:
    """Persist region config for a camera; returns the sanitized config."""
    clean = {
        "exclude": _sanitize_boxes(config.get("exclude")),
        "include": _sanitize_boxes(config.get("include")),
        "use_seg_mask": bool(config.get("use_seg_mask")),
    }
    os.makedirs(_regions_dir(), exist_ok=True)
    with open(_regions_path(camera_id), "w") as fh:
        json.dump(clean, fh, indent=2)
    return clean


def clear_regions(camera_id: int) -> None:
    try:
        os.remove(_regions_path(camera_id))
    except FileNotFoundError:
        pass


def _boxes_to_pixels(boxes: list, shape: tuple) -> list:
    h, w = shape[:2]
    out = []
    for x1, y1, x2, y2 in boxes:
        out.append((int(x1 * w), int(y1 * h), max(int(x2 * w), int(x1 * w) + 1),
                    max(int(y2 * h), int(y1 * h) + 1)))
    return out


def build_registration_mask(
    camera_id: int,
    target_shape: tuple,
    use_seg_mask: bool = True,
    seg_mask_loader: Optional[Callable[[int, tuple], Optional[np.ndarray]]] = None,
) -> Optional[np.ndarray]:
    """
    Build the effective registration mask for a camera.

    Args:
        camera_id: camera to build the mask for.
        target_shape: (height, width) of the frame being registered.
        use_seg_mask: whether segmentation masks are enabled for this run
            (the per-camera config can additionally require them).
        seg_mask_loader: callable returning the camera's saved segmentation
            mask (or None); injected to avoid a circular import.

    Returns:
        uint8 mask (255 = keep) or None when there are no constraints.
    """
    config = load_regions(camera_id)
    h, w = target_shape[:2]

    include = _boxes_to_pixels(config["include"], (h, w))
    exclude = _boxes_to_pixels(config["exclude"], (h, w))

    seg = None
    if use_seg_mask or config["use_seg_mask"]:
        if seg_mask_loader is not None:
            try:
                seg = seg_mask_loader(camera_id, (h, w))
            except Exception as exc:
                logger.warning(f"Seg mask load failed for camera {camera_id}: {exc}")

    if not include and not exclude and seg is None:
        return None

    if include:
        mask = np.zeros((h, w), dtype=np.uint8)
        for x1, y1, x2, y2 in include:
            mask[y1:y2, x1:x2] = 255
    else:
        mask = np.full((h, w), 255, dtype=np.uint8)

    if seg is not None:
        if seg.shape[:2] != (h, w):
            import cv2

            seg = cv2.resize(seg, (w, h), interpolation=cv2.INTER_NEAREST)
        mask[seg < 128] = 0

    # Exclusions always win — applied last so text/OSD areas can never
    # contribute registration points even inside include boxes or segments.
    for x1, y1, x2, y2 in exclude:
        mask[y1:y2, x1:x2] = 0

    if not mask.any():
        logger.warning(f"Camera {camera_id}: registration mask is empty; "
                       "ignoring constraints for this frame")
        return None
    return mask
