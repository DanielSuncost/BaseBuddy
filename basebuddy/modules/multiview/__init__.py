"""
Multiview 3D Reconstruction Module
Provides camera calibration and 3D reconstruction from multiple synchronized views

Features:
- Time-synchronized frame retrieval from timelapse images
- Live camera capture with synchronization
- Multiple matching methods (SIFT, LoFTR, hybrid)
- Auto-calibration using feature matching
- Reinforcement learning for adaptive calibration
- State-of-the-art 3D reconstruction
"""

from .calibration import CameraCalibrator, MultiviewCalibration, AutoCalibrator
from .reconstruction import MultiviewReconstructor
from .sync import MultiviewFrameSync, MultiviewSession
from .matcher import FeatureMatcher, MatchResult, MatchingMethod, AdaptiveCalibrationRL

__all__ = [
    'CameraCalibrator', 
    'MultiviewCalibration', 
    'AutoCalibrator', 
    'MultiviewReconstructor',
    'MultiviewFrameSync',
    'MultiviewSession',
    'FeatureMatcher',
    'MatchResult',
    'MatchingMethod',
    'AdaptiveCalibrationRL'
]

