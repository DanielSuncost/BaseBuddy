"""Common interface for 3D reconstruction engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

# progress_cb(percent 0-99, message)
ProgressCb = Callable[[int, str], None]


@dataclass
class EngineResult:
    points: np.ndarray            # (N, 3) float32
    colors: np.ndarray            # (N, 3) uint8 RGB
    cameras: List[dict] = field(default_factory=list)  # [{'id', 'pose': 4x4 list}]
    extras: dict = field(default_factory=dict)


class ReconstructionEngine(ABC):
    id: str = ''
    label: str = ''
    description: str = ''

    @abstractmethod
    def available(self) -> bool:
        """Cheap check (imports / files only, never loads weights)."""

    @abstractmethod
    def reconstruct(self, images: List[np.ndarray],
                    masks: Optional[List[Optional[np.ndarray]]] = None,
                    progress_cb: Optional[ProgressCb] = None) -> EngineResult:
        """
        Args:
            images: BGR uint8 frames, one per camera view.
            masks: optional per-view binary masks (255=keep); engines may use
                them to focus or crop, and to filter output points.
            progress_cb: optional progress reporter.
        Raises on failure; returns EngineResult on success.
        """


def report(progress_cb: Optional[ProgressCb], pct: int, msg: str) -> None:
    if progress_cb:
        progress_cb(pct, msg)


def apply_mask_filter(points: np.ndarray, colors: np.ndarray,
                      per_pixel_keep: np.ndarray) -> tuple:
    """Filter flattened per-pixel points by a boolean keep array."""
    keep = per_pixel_keep.reshape(-1)
    return points[keep], colors[keep]
