from basebuddy.core.inference.router import InferenceRouter, get_inference_router
from basebuddy.core.inference.types import (
    BoundingBox,
    ClassificationResult,
    Detection,
    DetectionResult,
    SegmentationResult,
    coco_class_name,
)

__all__ = [
    "BoundingBox",
    "ClassificationResult",
    "Detection",
    "DetectionResult",
    "InferenceRouter",
    "SegmentationResult",
    "coco_class_name",
    "get_inference_router",
]
