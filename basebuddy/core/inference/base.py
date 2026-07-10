"""Abstract inference provider interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

import numpy as np

from basebuddy.core.inference.types import ClassificationResult, DetectionResult, SegmentationResult


class DetectionProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        ...

    @abstractmethod
    def detect(
        self,
        frame: np.ndarray,
        *,
        camera_id: int,
        model_id: Optional[str] = None,
        is_dark_mode: bool = False,
        conf_threshold: float = 0.35,
    ) -> DetectionResult:
        ...

    def supports_model(self, model_id: str) -> bool:
        return True


class SegmentationProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        ...

    @abstractmethod
    def segment(
        self,
        frame: np.ndarray,
        *,
        points: List[Tuple[float, float]],
        labels: List[int],
        model_id: str = "sam_vit_b",
    ) -> SegmentationResult:
        ...


class ClassificationProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        ...

    @abstractmethod
    def classify(
        self,
        crop: np.ndarray,
        *,
        model_id: str,
    ) -> ClassificationResult:
        ...
