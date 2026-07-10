"""
DUSt3R engine (CVPR 2024) - kept as a fallback for the newer feed-forward
models (VGGT / Pi3). Pairwise pointmap prediction + global alignment.

Fixes over the legacy wrapper: the model is loaded once and cached (it is
2.2 GB), colors are sampled from the model-resolution images so they line up
with the predicted points, temp dirs are unique per request, and confidence
filtering uses a percentile instead of a fixed threshold.
"""
from __future__ import annotations

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

MODEL_NAME = os.environ.get('DUST3R_MODEL', 'naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt')
CONF_PERCENTILE = 30.0

_model = None
_model_lock = threading.Lock()


def _dust3r_available() -> bool:
    # Importing the wrapper performs the DUST3R_PATH sys.path setup.
    try:
        from basebuddy.modules.multiview.dust3r_wrapper import DUST3R_AVAILABLE
        return bool(DUST3R_AVAILABLE)
    except Exception:
        return False


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            import torch
            from dust3r.model import AsymmetricCroCo3DStereo
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            logger.info(f'Loading DUSt3R model {MODEL_NAME} on {device}...')
            _model = AsymmetricCroCo3DStereo.from_pretrained(MODEL_NAME).to(device).eval()
            logger.info('DUSt3R model loaded')
        return _model


def _make_pairs(imgs):
    n = len(imgs)
    pairs = []
    if n <= 4:
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((imgs[i], imgs[j]))
    else:
        for i in range(n - 1):
            pairs.append((imgs[i], imgs[i + 1]))
        for i in range(n - 2):
            pairs.append((imgs[i], imgs[i + 2]))
    return pairs


class Dust3REngine(ReconstructionEngine):
    id = 'dust3r'
    label = 'DUSt3R (2024)'
    description = ('Pairwise dense stereo pointmaps with global alignment. Slower than '
                   'the feed-forward engines (runs an optimization loop) but well tested.')

    def available(self) -> bool:
        return _dust3r_available()

    def reconstruct(self, images: List[np.ndarray],
                    masks: Optional[List[Optional[np.ndarray]]] = None,
                    progress_cb: Optional[ProgressCb] = None) -> EngineResult:
        import torch
        from dust3r.inference import inference
        from dust3r.utils.image import load_images
        from dust3r.cloud_opt import global_aligner, GlobalAlignerMode

        report(progress_cb, 5, 'Loading DUSt3R model')
        model = _get_model()
        device = str(next(model.parameters()).device)

        report(progress_cb, 15, f'Preprocessing {len(images)} views')
        tmp = tempfile.mkdtemp(prefix='dust3r_')
        try:
            paths = []
            for i, img in enumerate(images):
                p = os.path.join(tmp, f'view_{i:02d}.jpg')
                cv2.imwrite(p, img)
                paths.append(p)
            imgs = load_images(paths, size=512)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        report(progress_cb, 25, 'Running pairwise inference')
        pairs = _make_pairs(imgs)
        output = inference(pairs, model, device, batch_size=1)

        report(progress_cb, 55, 'Global alignment (optimization)')
        scene = global_aligner(output, device=device,
                               mode=GlobalAlignerMode.PointCloudOptimizer)
        scene.compute_global_alignment(init='mst', niter=300, schedule='cosine', lr=0.01)

        report(progress_cb, 85, 'Extracting point cloud')
        pts3d = scene.get_pts3d()
        confidences = scene.im_conf
        # scene.imgs are the model-resolution RGB images ([0,1]) matching pts3d.
        scene_imgs = scene.imgs

        all_pts, all_cols = [], []
        for view_idx, (pts, conf, img) in enumerate(zip(pts3d, confidences, scene_imgs)):
            pts_np = pts.detach().cpu().numpy().reshape(-1, 3)
            conf_np = conf.detach().cpu().numpy().reshape(-1)
            cols_np = np.clip(np.asarray(img).reshape(-1, 3) * 255.0, 0, 255).astype(np.uint8)

            keep = conf_np >= np.percentile(conf_np, CONF_PERCENTILE)

            if masks is not None and view_idx < len(masks) and masks[view_idx] is not None:
                h, w = np.asarray(img).shape[:2]
                resized = cv2.resize(masks[view_idx], (w, h), interpolation=cv2.INTER_NEAREST)
                keep &= resized.reshape(-1) > 127

            all_pts.append(pts_np[keep])
            all_cols.append(cols_np[keep])

        cameras = []
        try:
            for i, pose in enumerate(scene.get_im_poses()):
                cameras.append({'id': i, 'pose': pose.detach().cpu().numpy().tolist()})
        except Exception as e:
            logger.warning(f'DUSt3R camera pose extraction failed: {e}')

        return EngineResult(points=np.vstack(all_pts).astype(np.float32),
                            colors=np.vstack(all_cols),
                            cameras=cameras,
                            extras={'model': MODEL_NAME})
