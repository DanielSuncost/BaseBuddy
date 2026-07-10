"""Routes inference requests to local or cloud providers."""
from __future__ import annotations

import threading
from typing import Dict, Optional

import numpy as np

from basebuddy.core.inference.base import ClassificationProvider, DetectionProvider, SegmentationProvider
from basebuddy.core.inference.cloud.client import CloudClient
from basebuddy.core.inference.cloud.detection import CloudDetectionProvider
from basebuddy.core.inference.exceptions import CloudNotConfigured, ResourceExhausted
from basebuddy.core.inference.local.sam_segmentation import LocalSamSegmentationProvider
from basebuddy.core.inference.local.yolo_detection import LocalYoloDetectionProvider
from basebuddy.core.inference.types import ClassificationResult, DetectionResult, SegmentationResult


class InferenceRouter:
    def __init__(
        self,
        mode: str = "local",
        hybrid_fallback: bool = True,
        cloud_client: Optional[CloudClient] = None,
    ):
        self.mode = (mode or "local").lower()
        self.hybrid_fallback = hybrid_fallback
        self._local_detection: Dict[int, LocalYoloDetectionProvider] = {}
        self._cloud_detection: Optional[CloudDetectionProvider] = None
        self._segmentation: Optional[LocalSamSegmentationProvider] = None
        self._cloud_client = cloud_client
        self._lock = threading.Lock()

        if cloud_client is not None:
            self._cloud_detection = CloudDetectionProvider(cloud_client)

    def _local_provider(self, camera_id: int) -> LocalYoloDetectionProvider:
        with self._lock:
            if camera_id not in self._local_detection:
                self._local_detection[camera_id] = LocalYoloDetectionProvider(camera_id)
            return self._local_detection[camera_id]

    def get_local_provider(self, camera_id: int) -> LocalYoloDetectionProvider:
        return self._local_provider(camera_id)

    def release_camera(self, camera_id: int) -> None:
        with self._lock:
            provider = self._local_detection.pop(camera_id, None)
            if provider is not None:
                provider.release()

    def detect(
        self,
        frame: np.ndarray,
        *,
        camera_id: int,
        model_id: Optional[str] = None,
        is_dark_mode: bool = False,
        conf_threshold: float = 0.35,
    ) -> DetectionResult:
        if self.mode == "cloud":
            if self._cloud_detection is None:
                raise CloudNotConfigured("Cloud mode requires API key and endpoint")
            return self._cloud_detection.detect(
                frame,
                camera_id=camera_id,
                model_id=model_id,
                is_dark_mode=is_dark_mode,
                conf_threshold=conf_threshold,
            )

        local = self._local_provider(camera_id)
        try:
            return local.detect(
                frame,
                camera_id=camera_id,
                model_id=model_id,
                is_dark_mode=is_dark_mode,
                conf_threshold=conf_threshold,
            )
        except ResourceExhausted:
            if self.mode == "hybrid" and self.hybrid_fallback and self._cloud_detection is not None:
                return self._cloud_detection.detect(
                    frame,
                    camera_id=camera_id,
                    model_id=model_id,
                    is_dark_mode=is_dark_mode,
                    conf_threshold=conf_threshold,
                )
            raise

    def segment(self, frame: np.ndarray, *, points, labels, model_id: str = "sam_vit_b") -> SegmentationResult:
        if self._segmentation is None:
            self._segmentation = LocalSamSegmentationProvider()
        return self._segmentation.segment(frame, points=points, labels=labels, model_id=model_id)

    def classify(self, crop: np.ndarray, *, model_id: str) -> ClassificationResult:
        if self._cloud_detection is not None and self.mode in ("cloud", "hybrid"):
            import cv2

            ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if ok and self._cloud_client is not None:
                return self._cloud_client.classify(buf.tobytes(), model_id=model_id)
        raise CloudNotConfigured("Classification requires cloud provider")


_router: Optional[InferenceRouter] = None
_router_lock = threading.Lock()


def get_inference_router() -> InferenceRouter:
    global _router
    if _router is not None:
        return _router
    with _router_lock:
        if _router is None:
            from basebuddy.modules.config import (
                INFERENCE_CLOUD_API_KEY,
                INFERENCE_CLOUD_ENDPOINT,
                INFERENCE_CLOUD_TIMEOUT_S,
                INFERENCE_HYBRID_FALLBACK,
                INFERENCE_MODE,
            )

            client = None
            if INFERENCE_CLOUD_API_KEY and INFERENCE_CLOUD_ENDPOINT:
                client = CloudClient(
                    INFERENCE_CLOUD_ENDPOINT,
                    INFERENCE_CLOUD_API_KEY,
                    timeout_s=INFERENCE_CLOUD_TIMEOUT_S,
                )
            _router = InferenceRouter(
                mode=INFERENCE_MODE,
                hybrid_fallback=INFERENCE_HYBRID_FALLBACK,
                cloud_client=client,
            )
    return _router
