"""
VGGT engine (Visual Geometry Grounded Transformer, CVPR 2025).

Feed-forward: regresses camera poses, depth and dense world-space pointmaps
for all views in a single pass - no pairwise matching or global alignment.

Install:
    pip install git+https://github.com/facebookresearch/vggt.git

Weights download automatically from Hugging Face on first use. Default is
facebook/VGGT-1B; set VGGT_MODEL=facebook/VGGT-1B-Commercial (gated, requires
approval) for commercial deployments.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import tempfile
import threading
from typing import List, Optional

import cv2
import numpy as np

from basebuddy.modules.multiview.engines.base import (
    EngineResult, ProgressCb, ReconstructionEngine, report,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get('VGGT_MODEL', 'facebook/VGGT-1B')
# Keep points above this confidence percentile (VGGT conf is unbounded).
CONF_PERCENTILE = 30.0

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            import torch
            from vggt.models.vggt import VGGT
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            logger.info(f'Loading VGGT model {DEFAULT_MODEL} on {device}...')
            _model = VGGT.from_pretrained(DEFAULT_MODEL).to(device).eval()
            logger.info('VGGT model loaded')
        return _model


class VGGTEngine(ReconstructionEngine):
    id = 'vggt'
    label = 'VGGT (2025, feed-forward)'
    description = ('Meta\'s Visual Geometry Grounded Transformer: cameras, depth and '
                   'dense point maps for all views in one pass. Commercial checkpoint '
                   'available (set VGGT_MODEL=facebook/VGGT-1B-Commercial).')

    def available(self) -> bool:
        return importlib.util.find_spec('vggt') is not None

    def reconstruct(self, images: List[np.ndarray],
                    masks: Optional[List[Optional[np.ndarray]]] = None,
                    progress_cb: Optional[ProgressCb] = None) -> EngineResult:
        import torch
        from vggt.utils.load_fn import load_and_preprocess_images

        report(progress_cb, 5, 'Loading VGGT model')
        model = _get_model()
        device = next(model.parameters()).device

        report(progress_cb, 20, f'Preprocessing {len(images)} views')
        tmp = tempfile.mkdtemp(prefix='vggt_')
        try:
            paths = []
            for i, img in enumerate(images):
                p = os.path.join(tmp, f'view_{i:02d}.png')
                cv2.imwrite(p, img)
                paths.append(p)
            batch = load_and_preprocess_images(paths).to(device)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        report(progress_cb, 35, 'Running VGGT inference')
        use_amp = device.type == 'cuda'
        amp_dtype = (torch.bfloat16 if use_amp and
                     torch.cuda.get_device_capability()[0] >= 8 else torch.float16)
        with torch.no_grad():
            if use_amp:
                with torch.cuda.amp.autocast(dtype=amp_dtype):
                    preds = model(batch)
            else:
                preds = model(batch)

        report(progress_cb, 75, 'Extracting point cloud')
        world_points = preds['world_points'].float().cpu().numpy()      # (1,N,H,W,3)
        conf = preds['world_points_conf'].float().cpu().numpy()          # (1,N,H,W)
        model_images = preds['images'].float().cpu().numpy()             # (1,N,3,H,W)

        world_points = world_points[0]
        conf = conf[0]
        rgb = np.transpose(model_images[0], (0, 2, 3, 1))                 # (N,H,W,3) in [0,1]

        pts = world_points.reshape(-1, 3)
        cols = np.clip(rgb.reshape(-1, 3) * 255.0, 0, 255).astype(np.uint8)
        conf_flat = conf.reshape(-1)

        keep = conf_flat >= np.percentile(conf_flat, CONF_PERCENTILE)

        if masks is not None:
            n, h, w = conf.shape
            mask_keep = np.ones(n * h * w, dtype=bool)
            for i, m in enumerate(masks[:n]):
                if m is None:
                    continue
                resized = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
                mask_keep[i * h * w:(i + 1) * h * w] = resized.reshape(-1) > 127
            keep &= mask_keep

        cameras = []
        try:
            from vggt.utils.pose_enc import pose_encoding_to_extri_intri
            extrinsic, intrinsic = pose_encoding_to_extri_intri(
                preds['pose_enc'], batch.shape[-2:])
            for i, ext in enumerate(extrinsic[0].float().cpu().numpy()):
                pose = np.eye(4)
                pose[:3, :4] = ext
                cameras.append({'id': i, 'pose': pose.tolist()})
        except Exception as e:
            logger.warning(f'VGGT camera pose extraction failed: {e}')

        return EngineResult(points=pts[keep].astype(np.float32),
                            colors=cols[keep],
                            cameras=cameras,
                            extras={'model': DEFAULT_MODEL})
