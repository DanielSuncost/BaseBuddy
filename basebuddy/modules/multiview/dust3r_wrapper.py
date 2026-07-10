"""
DUSt3R Integration for Dense 3D Reconstruction

DUSt3R (Dense Unconstrained Stereo 3D Reconstruction) is a transformer-based
method that directly predicts 3D point maps from image pairs without requiring:
- Camera calibration
- Feature matching
- Known camera poses

This is state-of-the-art (2024) for sparse-view 3D reconstruction.

Installation:
    pip install dust3r  # Or clone from github.com/naver/dust3r

Reference: "DUSt3R: Geometric 3D Vision Made Easy" (Shuzhe Wang et al., CVPR 2024)
"""

import logging

logger = logging.getLogger(__name__)

import numpy as np
import cv2
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import os
import sys

# Add DUSt3R to path if installed locally (set DUST3R_PATH in .env to override)
_dust3r_env = os.environ.get("DUST3R_PATH", "").strip()
_DUST3R_PATHS = []
if _dust3r_env:
    _DUST3R_PATHS.append(Path(_dust3r_env).expanduser())
_DUST3R_PATHS.extend([
    Path(os.path.expanduser("~/Projects/dust3r")),
    Path(__import__("basebuddy.core.paths", fromlist=["get_repo_root"]).get_repo_root()) / "dust3r",
])

DUST3R_PATH = None
for _path in _DUST3R_PATHS:
    if _path.exists() and (_path / "dust3r").exists():
        DUST3R_PATH = _path
        if str(_path) not in sys.path:
            sys.path.insert(0, str(_path))
        break

if DUST3R_PATH is None:
    logger.warning(f"DUSt3R not found in: {[str(p) for p in _DUST3R_PATHS]}")

# Check if DUSt3R is available
DUST3R_AVAILABLE = False
try:
    from dust3r.inference import inference
    from dust3r.model import AsymmetricCroCo3DStereo
    from dust3r.utils.image import load_images
    from dust3r.cloud_opt import global_aligner, GlobalAlignerMode
    import torch
    DUST3R_AVAILABLE = True
    logger.info(f"DUSt3R loaded from {DUST3R_PATH if DUST3R_PATH else 'pip'}")
except ImportError as e:
    logger.warning(f"DUSt3R not available: {e}")


class DUSt3RReconstructor:
    """
    Dense 3D reconstruction using DUSt3R.
    
    Works with as few as 2 images, no calibration required.
    """
    
    def __init__(self, model_path: str = None, device: str = None):
        """
        Initialize DUSt3R.
        
        Args:
            model_path: Path to DUSt3R checkpoint (downloads if None)
            device: 'cuda' or 'cpu' (auto-detect if None)
        """
        if not DUST3R_AVAILABLE:
            raise ImportError(
                "DUSt3R not installed. Install with:\n"
                "  pip install dust3r\n"
                "Or clone from: https://github.com/naver/dust3r"
            )
        
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        
        # Load model
        if model_path is None:
            # Use default pretrained model
            model_path = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"
        
        logger.info(f"Loading DUSt3R model on {self.device}...")
        self.model = AsymmetricCroCo3DStereo.from_pretrained(model_path)
        self.model = self.model.to(self.device)
        self.model.eval()
        logger.info(f"DUSt3R loaded")
    
    def reconstruct_from_images(self, 
                                images: List[np.ndarray],
                                image_size: int = 512) -> Dict:
        """
        Reconstruct 3D from a list of images.
        
        Args:
            images: List of BGR images (numpy arrays)
            image_size: Resolution for processing (512 recommended)
            
        Returns:
            Dict with:
                - points_3d: Nx3 array of 3D points
                - colors: Nx3 array of RGB colors  
                - confidence: N array of confidence scores
                - cameras: List of camera parameters
        """
        if len(images) < 2:
            return {'success': False, 'error': 'Need at least 2 images'}
        
        # Save images temporarily for dust3r loading
        temp_dir = Path("/tmp/dust3r_temp")
        temp_dir.mkdir(exist_ok=True)
        
        image_paths = []
        for i, img in enumerate(images):
            path = temp_dir / f"img_{i}.jpg"
            cv2.imwrite(str(path), img)
            image_paths.append(str(path))
        
        try:
            # Load images in dust3r format
            imgs = load_images(image_paths, size=image_size)
            
            # Run inference
            pairs = self._make_pairs(imgs)
            output = inference(pairs, self.model, self.device, batch_size=1)
            
            # Global alignment
            scene = global_aligner(
                output, 
                device=self.device,
                mode=GlobalAlignerMode.PointCloudOptimizer
            )
            
            # Get optimized result
            scene.compute_global_alignment(
                init="mst",
                niter=300,
                schedule="cosine",
                lr=0.01
            )
            
            # Extract point cloud
            pts3d = scene.get_pts3d()
            confidence = scene.get_confidence()
            
            # Combine points from all views
            all_points = []
            all_colors = []
            all_conf = []
            
            for view_idx, (pts, conf, img) in enumerate(zip(pts3d, confidence, images)):
                pts_np = pts.detach().cpu().numpy().reshape(-1, 3)
                conf_np = conf.detach().cpu().numpy().flatten()
                
                # Get colors from original image
                h, w = img.shape[:2]
                colors = img.reshape(-1, 3)[:, ::-1]  # BGR to RGB
                
                # Filter by confidence
                mask = conf_np > 0.5
                
                all_points.append(pts_np[mask])
                all_colors.append(colors[mask])
                all_conf.append(conf_np[mask])
            
            points_3d = np.vstack(all_points)
            colors = np.vstack(all_colors)
            confidence = np.concatenate(all_conf)
            
            # Get camera poses
            cameras = []
            for i, pose in enumerate(scene.get_im_poses()):
                cameras.append({
                    'id': i,
                    'pose': pose.detach().cpu().numpy().tolist()
                })
            
            return {
                'success': True,
                'points_3d': points_3d,
                'colors': colors,
                'confidence': confidence,
                'num_points': len(points_3d),
                'cameras': cameras,
                'method': 'DUSt3R'
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}
        
        finally:
            # Cleanup temp files
            for p in image_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass
    
    def _make_pairs(self, imgs):
        """Create image pairs for DUSt3R inference"""
        pairs = []
        n = len(imgs)
        
        # For small number of images, pair all with all
        if n <= 4:
            for i in range(n):
                for j in range(i+1, n):
                    pairs.append((imgs[i], imgs[j]))
        else:
            # For larger sets, use sequential + skip pairs
            for i in range(n-1):
                pairs.append((imgs[i], imgs[i+1]))
            for i in range(n-2):
                pairs.append((imgs[i], imgs[i+2]))
        
        return pairs
    
    def save_point_cloud(self, result: Dict, output_path: str):
        """Save point cloud as PLY file"""
        if not result.get('success'):
            return False
        
        pts = result['points_3d']
        colors = result['colors']
        
        with open(output_path, 'w') as f:
            f.write("ply\n")
            f.write("format ascii 1.0\n")
            f.write(f"element vertex {len(pts)}\n")
            f.write("property float x\n")
            f.write("property float y\n")
            f.write("property float z\n")
            f.write("property uchar red\n")
            f.write("property uchar green\n")
            f.write("property uchar blue\n")
            f.write("end_header\n")
            
            for pt, col in zip(pts, colors):
                f.write(f"{pt[0]:.6f} {pt[1]:.6f} {pt[2]:.6f} ")
                f.write(f"{int(col[0])} {int(col[1])} {int(col[2])}\n")
        
        return True


def check_dust3r_available() -> Dict:
    """Check if DUSt3R is installed and working"""
    import torch
    
    info = {
        'available': DUST3R_AVAILABLE,
        'cuda': torch.cuda.is_available(),
    }
    
    if DUST3R_AVAILABLE:
        info['note'] = 'DUSt3R ready - best sparse-view 3D reconstruction'
    else:
        info['install'] = (
            "To install DUSt3R:\n"
            "  git clone https://github.com/naver/dust3r\n"
            "  cd dust3r\n"
            "  pip install -e .\n"
            "\n"
            "Or use pip (if available):\n"
            "  pip install dust3r"
        )
    
    return info

