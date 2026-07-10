"""
Classical structure-from-motion engine (SIFT + auto-calibration +
triangulation). No ML dependencies - always available. Produces sparse,
scale-free clouds; kept as the last-resort fallback and for CPU-only boxes.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from typing import List, Optional

import numpy as np

from basebuddy.modules.multiview.engines.base import (
    EngineResult, ProgressCb, ReconstructionEngine, report,
)

logger = logging.getLogger(__name__)


class SfMEngine(ReconstructionEngine):
    id = 'sfm'
    label = 'Classical SfM (SIFT)'
    description = ('Feature matching + auto-calibration + triangulation. No ML models '
                   'needed; sparse output, works best with strong texture overlap.')

    def available(self) -> bool:
        return True

    def reconstruct(self, images: List[np.ndarray],
                    masks: Optional[List[Optional[np.ndarray]]] = None,
                    progress_cb: Optional[ProgressCb] = None) -> EngineResult:
        from basebuddy.modules.multiview.calibration import AutoCalibrator, MultiviewCalibration
        from basebuddy.modules.multiview.reconstruction import MultiviewReconstructor

        cam_keys = [str(i) for i in range(len(images))]
        image_lists = {k: [img] for k, img in zip(cam_keys, images)}
        mask_lists = None
        if masks is not None and any(m is not None for m in masks):
            mask_lists = {k: [m] for k, m in zip(cam_keys, masks) if m is not None}

        # Calibration artifacts go to a throwaway dir; the persistent
        # CALIBRATION_DIR is only for user-run checkerboard calibrations.
        with tempfile.TemporaryDirectory(prefix='sfm_calib_') as calib_dir:
            report(progress_cb, 10, 'Estimating intrinsics')
            auto_calibrator = AutoCalibrator(calib_dir)
            calib_manager = MultiviewCalibration(calib_dir)
            for key, img in zip(cam_keys, images):
                K = auto_calibrator.estimate_intrinsics_from_image(img)
                calib_manager.save_intrinsic_calibration(key, {
                    'success': True,
                    'method': 'estimated_from_image_dimensions',
                    'camera_matrix': K.tolist(),
                    'distortion_coefficients': [[0, 0, 0, 0, 0]],
                    'image_size': (img.shape[1], img.shape[0]),
                    'calibration_date': datetime.now().isoformat(),
                })

            report(progress_cb, 30, 'Matching features and calibrating extrinsics')
            multiview_result = auto_calibrator.auto_calibrate_multiview(
                cam_keys, image_lists, mask_lists)
            if not multiview_result.get('success'):
                raise RuntimeError(
                    f"Auto-calibration failed: {multiview_result.get('error', 'unknown')}")

            report(progress_cb, 60, 'Triangulating points')
            reconstructor = MultiviewReconstructor(multiview_result, calib_dir)
            result = reconstructor.reconstruct_from_features(
                {k: v[0] for k, v in image_lists.items()},
                {k: v[0] for k, v in mask_lists.items()} if mask_lists else None)

        if not result.get('success'):
            raise RuntimeError(f"Reconstruction failed: {result.get('error', 'unknown')}")

        points = np.asarray(result['points_3d'], dtype=np.float32).reshape(-1, 3)
        colors = np.asarray(result['colors']).reshape(-1, 3)
        return EngineResult(points=points, colors=colors,
                            extras={'num_calibrated_pairs':
                                    multiview_result.get('num_calibrated_pairs', 0)})
