"""
Pi3 engine (π³: Permutation-Equivariant Visual Geometry Learning, ICLR 2026).

Feed-forward like VGGT but with no fixed reference view, which makes it more
robust to camera ordering - a good fit for unordered multi-camera rigs.

Install:
    git clone https://github.com/yyfz/Pi3 && pip install -e Pi3
    (or add the clone to PYTHONPATH / set PI3_PATH in .env)

Note: Pi3 weights are CC BY-NC 4.0 (non-commercial); code is BSD-3.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

from basebuddy.modules.multiview.engines.base import (
    EngineResult, ProgressCb, ReconstructionEngine, report,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get('PI3_MODEL', 'yyfz233/Pi3')
CONF_THRESHOLD = 0.1  # applied to sigmoid(conf), Pi3 demo default

# Allow a local clone like the DUSt3R convention.
_pi3_env = os.environ.get('PI3_PATH', '').strip()
for _cand in ([Path(_pi3_env).expanduser()] if _pi3_env else []) + [Path.home() / 'Projects' / 'Pi3']:
    if _cand.exists() and (_cand / 'pi3').exists() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))
        break

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            import torch
            from pi3.models.pi3 import Pi3
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
            logger.info(f'Loading Pi3 model {DEFAULT_MODEL} on {device}...')
            _model = Pi3.from_pretrained(DEFAULT_MODEL).to(device).eval()
            logger.info('Pi3 model loaded')
        return _model


class Pi3Engine(ReconstructionEngine):
    id = 'pi3'
    label = 'Pi3 (2026, reference-free)'
    description = ('Permutation-equivariant feed-forward reconstruction; no reference '
                   'view, robust to camera ordering. Weights are non-commercial '
                   '(research/personal use).')

    def available(self) -> bool:
        return importlib.util.find_spec('pi3') is not None

    def reconstruct(self, images: List[np.ndarray],
                    masks: Optional[List[Optional[np.ndarray]]] = None,
                    progress_cb: Optional[ProgressCb] = None) -> EngineResult:
        import torch

        report(progress_cb, 5, 'Loading Pi3 model')
        model = _get_model()
        device = next(model.parameters()).device

        report(progress_cb, 20, f'Preprocessing {len(images)} views')
        # Pi3 expects RGB float tensors in [0,1], dims multiple of 14 (ViT patch).
        tensors = []
        target_w = 1024
        for img in images:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            scale = min(1.0, target_w / float(w))
            nw = max(14, int(round(w * scale / 14)) * 14)
            nh = max(14, int(round(h * scale / 14)) * 14)
            rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
            tensors.append(torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0)
        # All views must share a shape; resize to the first view's shape.
        shape = tensors[0].shape[-2:]
        tensors = [t if t.shape[-2:] == shape else
                   torch.nn.functional.interpolate(t[None], size=shape, mode='bilinear')[0]
                   for t in tensors]
        batch = torch.stack(tensors)[None].to(device)  # (1,N,3,H,W)

        report(progress_cb, 35, 'Running Pi3 inference')
        use_amp = device.type == 'cuda'
        amp_dtype = (torch.bfloat16 if use_amp and
                     torch.cuda.get_device_capability()[0] >= 8 else torch.float16)
        with torch.no_grad():
            if use_amp:
                with torch.cuda.amp.autocast(dtype=amp_dtype):
                    res = model(batch)
            else:
                res = model(batch)

        report(progress_cb, 75, 'Extracting point cloud')
        points = res['points'][0].float().cpu().numpy()          # (N,H,W,3)
        conf = torch.sigmoid(res['conf'][0]).float().cpu().numpy()  # (N,H,W,1)

        n, h, w = points.shape[:3]
        rgb_np = (batch[0].permute(0, 2, 3, 1).cpu().numpy() * 255.0)
        pts = points.reshape(-1, 3)
        cols = np.clip(rgb_np.reshape(-1, 3), 0, 255).astype(np.uint8)
        keep = conf.reshape(-1) > CONF_THRESHOLD

        if masks is not None:
            mask_keep = np.ones(n * h * w, dtype=bool)
            for i, m in enumerate(masks[:n]):
                if m is None:
                    continue
                resized = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
                mask_keep[i * h * w:(i + 1) * h * w] = resized.reshape(-1) > 127
            keep &= mask_keep

        cameras = []
        try:
            for i, pose in enumerate(res['camera_poses'][0].float().cpu().numpy()):
                cameras.append({'id': i, 'pose': np.asarray(pose).reshape(4, 4).tolist()})
        except Exception as e:
            logger.warning(f'Pi3 camera pose extraction failed: {e}')

        return EngineResult(points=pts[keep].astype(np.float32),
                            colors=cols[keep],
                            cameras=cameras,
                            extras={'model': DEFAULT_MODEL})
