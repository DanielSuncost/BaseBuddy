"""Local YOLO detection via Ultralytics."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Tuple

import numpy as np

from basebuddy.core.inference.base import DetectionProvider
from basebuddy.core.inference.exceptions import ResourceExhausted
from basebuddy.core.inference.types import BoundingBox, Detection, DetectionResult, coco_class_name

# Models are shared across cameras: loading a separate copy of each model per
# camera multiplies memory by the camera count for zero benefit. Inference on a
# shared model is serialized with a per-model lock (Ultralytics predictors are
# not thread-safe).
_model_cache: Dict[Tuple[str, str], object] = {}
_model_locks: Dict[Tuple[str, str], threading.Lock] = {}
_cache_lock = threading.Lock()


def _get_shared_model(path: str, device: str):
    """Load a YOLO model once per (path, device) and reuse it across cameras."""
    from ultralytics import YOLO

    key = (path, device)
    with _cache_lock:
        model = _model_cache.get(key)
        if model is None:
            model = YOLO(path)
            model.to(device)
            _model_cache[key] = model
            _model_locks[key] = threading.Lock()
        return model, _model_locks[key]


def resolve_model_path(model_name: str) -> str:
    """Map .pt to tensorrt/openvino export if configured and file exists."""
    from basebuddy.modules.config import INFERENCE_BACKEND

    if not model_name:
        return model_name
    backend = (INFERENCE_BACKEND or "pt").lower()
    base, ext = os.path.splitext(model_name)
    if backend == "tensorrt":
        candidate = f"{base}.engine"
        if os.path.isfile(candidate):
            return candidate
    if backend == "openvino":
        for suffix in ("_openvino_model", ".xml"):
            candidate = f"{base}{suffix}" if suffix.startswith("_") else base + suffix
            if os.path.isdir(candidate) or os.path.isfile(candidate):
                return candidate
    return model_name


class LocalYoloDetectionProvider(DetectionProvider):
    """Per-camera YOLO provider with adaptive day/night models."""

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.day_model = None
        self.night_model = None
        self._day_lock: Optional[threading.Lock] = None
        self._night_lock: Optional[threading.Lock] = None
        self._active_is_night = False
        self._load_models()

    @property
    def provider_id(self) -> str:
        return "local"

    @property
    def active_model(self):
        if self._active_is_night and self.night_model is not None:
            return self.night_model
        return self.day_model

    @property
    def model_id(self) -> str:
        from basebuddy.modules.config import ADAPTIVE_MODE, AI_MODEL, DAY_MODEL, NIGHT_MODEL

        if not ADAPTIVE_MODE:
            return AI_MODEL
        return NIGHT_MODEL if self._active_is_night else DAY_MODEL

    def _load_models(self) -> None:
        import logging
        logger = logging.getLogger(__name__)
        try:
            from basebuddy.modules.config import ADAPTIVE_MODE, AI_MODEL, DAY_MODEL, NIGHT_MODEL
            import torch

            max_gpu_cameras = int(os.environ.get("MAX_GPU_CAMERAS", "4"))
            if torch.cuda.is_available() and self.camera_id < max_gpu_cameras:
                device = "cuda:0"
                device_name = "GPU (CUDA:0)"
            else:
                device = "cpu"
                device_name = "CPU"

            if not ADAPTIVE_MODE:
                path = resolve_model_path(AI_MODEL)
                self.day_model, self._day_lock = _get_shared_model(path, device)
                self.night_model, self._night_lock = self.day_model, self._day_lock
                logger.info("Camera %d: shared model on %s: %s", self.camera_id + 1, device_name, path)
                return

            day_path = resolve_model_path(DAY_MODEL)
            night_path = resolve_model_path(NIGHT_MODEL)
            self.day_model, self._day_lock = _get_shared_model(day_path, device)
            self.night_model, self._night_lock = _get_shared_model(night_path, device)
            logger.info(
                "Camera %d: shared day/night models on %s: %s / %s",
                self.camera_id + 1, device_name, day_path, night_path,
            )
        except Exception as exc:
            logger.error("Camera %d: failed to load YOLO models: %s", self.camera_id + 1, exc)
            try:
                from basebuddy.modules.config import AI_MODEL

                self.day_model, self._day_lock = _get_shared_model(AI_MODEL, "cpu")
                self.night_model, self._night_lock = self.day_model, self._day_lock
                logger.warning("Camera %d: fallback model on CPU: %s", self.camera_id + 1, AI_MODEL)
            except Exception as exc2:
                logger.error("Camera %d: fallback model load failed: %s", self.camera_id + 1, exc2)

    def release(self) -> None:
        # Models are shared across cameras; just drop this provider's references.
        self.day_model = None
        self.night_model = None
        self._day_lock = None
        self._night_lock = None

    def detect(
        self,
        frame: np.ndarray,
        *,
        camera_id: int,
        model_id: Optional[str] = None,
        is_dark_mode: bool = False,
        conf_threshold: float = 0.35,
    ) -> DetectionResult:
        self._active_is_night = is_dark_mode
        model = self.active_model
        if model is None:
            return DetectionResult([], 0.0, model_id or "none", self.provider_id)

        gpu_granted = False
        resource_manager = None
        try:
            from basebuddy.modules.resource_manager import get_resource_manager, ResourcePriority

            resource_manager = get_resource_manager()
            gpu_stats = resource_manager.monitor.get_gpu_stats()
            if gpu_stats and gpu_stats.memory_utilization_percent > 92:
                raise ResourceExhausted(
                    f"GPU memory critically low ({gpu_stats.memory_utilization_percent:.1f}%)"
                )

            gpu_granted = resource_manager.request_gpu_access(
                requester_id=f"detection_cam_{camera_id}",
                priority=ResourcePriority.CRITICAL,
                estimated_memory_mb=500.0,
                timeout_seconds=0.1,
                blocking=False,
            )
            if not gpu_granted and gpu_stats and gpu_stats.memory_utilization_percent > 90:
                raise ResourceExhausted(
                    f"GPU access denied, memory at {gpu_stats.memory_utilization_percent:.1f}%"
                )
        except ResourceExhausted:
            raise
        except Exception:
            pass

        model_lock = self._night_lock if (self._active_is_night and self.night_model is not None) else self._day_lock
        start = time.time()
        try:
            if model_lock is not None:
                with model_lock:
                    results = model(frame, verbose=False)[0]
            else:
                results = model(frame, verbose=False)[0]
        finally:
            if gpu_granted and resource_manager is not None:
                try:
                    resource_manager.release_gpu_access(f"detection_cam_{camera_id}")
                except Exception:
                    pass

        inference_ms = (time.time() - start) * 1000.0
        detections: list[Detection] = []

        if results.boxes is None or len(results.boxes) == 0:
            return DetectionResult(detections, inference_ms, self.model_id, self.provider_id, raw=results)

        boxes = results.boxes.xyxy.cpu().numpy()
        class_ids = results.boxes.cls.cpu().numpy().astype(int)
        confidences = results.boxes.conf.cpu().numpy()

        for box, class_id, conf in zip(boxes, class_ids, confidences):
            x1, y1, x2, y2 = map(float, box)
            cid = int(class_id)
            detections.append(
                Detection(
                    bbox=BoundingBox(x1, y1, x2, y2),
                    class_id=cid,
                    class_name=coco_class_name(cid),
                    confidence=float(conf),
                )
            )

        return DetectionResult(detections, inference_ms, self.model_id, self.provider_id, raw=results)
