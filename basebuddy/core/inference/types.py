"""Shared inference result types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class BoundingBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass
class Detection:
    bbox: BoundingBox
    class_id: int
    class_name: str
    confidence: float
    track_id: Optional[int] = None


@dataclass
class DetectionResult:
    detections: List[Detection]
    inference_ms: float
    model_id: str
    provider: str
    raw: Optional[Any] = None


@dataclass
class SegmentationResult:
    mask: Any  # np.ndarray H×W uint8
    coverage: float
    inference_ms: float
    model_id: str
    provider: str


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    inference_ms: float
    model_id: str
    provider: str


COCO_CLASSES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
    'train', 'truck', 'boat', 'traffic light', 'fire hydrant',
    'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
    'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe',
    'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
    'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat',
    'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot',
    'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
    'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop',
    'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
    'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock',
    'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush',
]


def coco_class_name(class_id: int) -> str:
    idx = int(class_id)
    if 0 <= idx < len(COCO_CLASSES):
        return COCO_CLASSES[idx]
    return f"class_{idx}"
