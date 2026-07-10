"""Local SAM segmentation (Segment Anything)."""
from __future__ import annotations

import time
from typing import List, Tuple

import numpy as np

from basebuddy.core.inference.base import SegmentationProvider
from basebuddy.core.inference.types import SegmentationResult
import logging

logger = logging.getLogger(__name__)

_sam_predictor = None


def get_sam_predictor():
    """Lazy-load SAM predictor (shared across calls)."""
    global _sam_predictor
    if _sam_predictor is not None:
        return _sam_predictor

    try:
        import torch
        from segment_anything import sam_model_registry, SamPredictor

        checkpoint = "sam_vit_b_01ec64.pth"
        cuda_available = torch.cuda.is_available()
        device = "cuda" if cuda_available else "cpu"
        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
            logger.info(f"GPU detected for SAM: {gpu_name} ({gpu_mem:.1f}GB)")
        else:
            logger.warning("CUDA not available for SAM, using CPU")

        logger.info(f"Loading SAM model on {device}...")
        sam = sam_model_registry["vit_b"](checkpoint=checkpoint)
        sam.to(device=device)
        sam.eval()
        _sam_predictor = SamPredictor(sam)
        logger.info(f"SAM loaded on {device}")
    except Exception as exc:
        logger.warning(f"SAM not available: {exc}")
        _sam_predictor = None

    return _sam_predictor


class LocalSamSegmentationProvider(SegmentationProvider):
    @property
    def provider_id(self) -> str:
        return "local"

    def segment(
        self,
        frame: np.ndarray,
        *,
        points: List[Tuple[float, float]],
        labels: List[int],
        model_id: str = "sam_vit_b",
    ) -> SegmentationResult:
        empty = np.zeros(frame.shape[:2], dtype=np.uint8)
        predictor = get_sam_predictor()
        if predictor is None or not points:
            return SegmentationResult(empty, 0.0, 0.0, model_id, self.provider_id)

        start = time.time()
        predictor.set_image(frame)
        point_coords = np.array(points)
        point_labels = np.array(labels)
        masks, _scores, _logits = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=False,
        )
        mask = (masks[0] * 255).astype(np.uint8)
        coverage = float(np.sum(mask > 0) / max(1, mask.shape[0] * mask.shape[1]))
        inference_ms = (time.time() - start) * 1000.0
        return SegmentationResult(mask, coverage, inference_ms, model_id, self.provider_id)
