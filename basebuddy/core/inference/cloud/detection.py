"""Cloud detection provider (delegates to CloudClient)."""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from basebuddy.core.inference.base import DetectionProvider
from basebuddy.core.inference.cloud.client import CloudClient
from basebuddy.core.inference.exceptions import CloudNotConfigured
from basebuddy.core.inference.types import DetectionResult


class CloudDetectionProvider(DetectionProvider):
    def __init__(self, client: CloudClient):
        self.client = client

    @property
    def provider_id(self) -> str:
        return "cloud"

    def detect(
        self,
        frame: np.ndarray,
        *,
        camera_id: int,
        model_id: Optional[str] = None,
        is_dark_mode: bool = False,
        conf_threshold: float = 0.35,
    ) -> DetectionResult:
        if not self.client._configured():
            raise CloudNotConfigured("Cloud inference not configured")

        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return DetectionResult([], 0.0, model_id or "yolov8s", self.provider_id)

        return self.client.detect(
            buf.tobytes(),
            camera_id=camera_id,
            model_id=model_id or "yolov8s",
            conf_threshold=conf_threshold,
        )
