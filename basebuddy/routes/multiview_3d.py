"""
Multiview 3D Reconstruction Routes
API endpoints for camera calibration and 3D plant reconstruction
"""

import logging

logger = logging.getLogger(__name__)

from flask import Blueprint, request, jsonify, send_file
import os
import glob
import json
import cv2
import numpy as np
from datetime import datetime
from pathlib import Path
import io

multiview_3d_bp = Blueprint('multiview_3d', __name__)


# ============ HTML PAGE ROUTE ============

@multiview_3d_bp.route('/multiview-3d')
def multiview_3d_page():
    """Render the Multiview 3D reconstruction page"""
    from flask import render_template
    return render_template('multiview_3d.html', active_page='multiview_3d')


# Configuration - Use absolute paths
from basebuddy.core.paths import get_repo_root

_REPO = get_repo_root()
_BASE_DIR = _REPO
MULTIVIEW_DIR = os.path.join(_REPO, "multiview_data")
CALIBRATION_DIR = os.path.join(MULTIVIEW_DIR, "calibration")
RECONSTRUCTIONS_DIR = os.path.join(MULTIVIEW_DIR, "reconstructions")
CALIBRATION_IMAGES_DIR = os.path.join(MULTIVIEW_DIR, "calibration_images")

# Create directories
for d in [MULTIVIEW_DIR, CALIBRATION_DIR, RECONSTRUCTIONS_DIR, CALIBRATION_IMAGES_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)
logger.info(f"[Multiview] Data directory: {MULTIVIEW_DIR}")


# ============ HELPER FUNCTIONS ============

def load_plant_mask(camera_id: int, target_shape: tuple) -> np.ndarray:
    """
    Load plant segmentation mask - tries multiple sources:
    1. Interactive multiview masks (saved from this UI)
    2. Plant tracking system masks
    
    Args:
        camera_id: Camera ID to load mask for
        target_shape: (height, width) to resize mask to
        
    Returns:
        Binary mask (255 = plant, 0 = background) or None if not available
    """
    mask = None
    
    # Try 1: Interactive multiview mask (most recent)
    multiview_mask_path = os.path.join(MULTIVIEW_DIR, "masks", f"camera_{camera_id}_mask.png")
    if os.path.exists(multiview_mask_path):
        mask = cv2.imread(multiview_mask_path, cv2.IMREAD_GRAYSCALE)
        if mask is not None:
            logger.info(f"[Multiview] Loaded interactive mask for camera {camera_id}")
    
    # Try 2: Plant tracking masks
    if mask is None:
        mask_dir = os.path.join(_BASE_DIR, f"plant_segmentation_results/camera_{camera_id}/masks")
        if os.path.exists(mask_dir):
            mask_files = sorted(glob.glob(os.path.join(mask_dir, "*_mask.png")), reverse=True)
            if mask_files:
                mask = cv2.imread(mask_files[0], cv2.IMREAD_GRAYSCALE)
                if mask is not None:
                    logger.info(f"[Multiview] Loaded plant tracking mask for camera {camera_id}")
    
    if mask is None:
        return None
    
    # Resize to match frame if needed
    if mask.shape[:2] != target_shape:
        mask = cv2.resize(mask, (target_shape[1], target_shape[0]))
    
    # Ensure binary
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
    
    return mask


def get_registration_mask(camera_id: int, target_shape: tuple,
                          use_seg_mask: bool = True) -> np.ndarray:
    """
    Effective registration mask for a camera: user-drawn include/exclude
    regions (see registration_regions module) combined with the saved
    segmentation mask. Exclude regions (e.g. burned-in timestamps/logos)
    are applied even when segmentation masks are disabled for the run.

    Returns uint8 mask (255 = usable for registration) or None.
    """
    from basebuddy.modules.multiview.registration_regions import build_registration_mask

    return build_registration_mask(
        camera_id, target_shape,
        use_seg_mask=use_seg_mask,
        seg_mask_loader=load_plant_mask,
    )


def create_plant_color_mask(image: np.ndarray) -> np.ndarray:
    """
    Create a mask based on plant-like colors (greens and browns).
    
    This is a fallback when segmentation masks aren't available.
    Uses HSV color space to detect:
    - Greens: H=35-85, S>30, V>30
    - Browns/stems: H=10-30, S>20, V>30
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Green range (main plant color)
    green_lower = np.array([35, 30, 30])
    green_upper = np.array([85, 255, 255])
    green_mask = cv2.inRange(hsv, green_lower, green_upper)
    
    # Brown/stem range
    brown_lower = np.array([10, 20, 30])
    brown_upper = np.array([30, 200, 200])
    brown_mask = cv2.inRange(hsv, brown_lower, brown_upper)
    
    # Yellow-green (young leaves)
    yellow_green_lower = np.array([25, 40, 40])
    yellow_green_upper = np.array([40, 255, 255])
    yellow_green_mask = cv2.inRange(hsv, yellow_green_lower, yellow_green_upper)
    
    # Combine masks
    combined = cv2.bitwise_or(green_mask, brown_mask)
    combined = cv2.bitwise_or(combined, yellow_green_mask)
    
    # Clean up with morphology
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, kernel)
    
    # Dilate slightly to ensure features on edges are included
    combined = cv2.dilate(combined, kernel, iterations=2)
    
    return combined


def add_mask_overlay_to_viz(viz: np.ndarray, img1: np.ndarray, img2: np.ndarray,
                           mask1: np.ndarray, mask2: np.ndarray) -> np.ndarray:
    """Add a subtle mask overlay to the visualization to show masked regions."""
    h1, w1 = img1.shape[:2]
    
    # Create colored overlay for mask regions
    overlay = viz.copy()
    
    if mask1 is not None:
        # Green tint for mask1 region
        mask1_colored = np.zeros_like(img1)
        mask1_colored[:, :, 1] = 50  # Green channel
        mask1_region = cv2.bitwise_and(mask1_colored, mask1_colored, mask=mask1)
        overlay[:h1, :w1] = cv2.addWeighted(overlay[:h1, :w1], 0.85, mask1_region, 0.15, 0)
    
    if mask2 is not None:
        # Green tint for mask2 region
        mask2_colored = np.zeros_like(img2)
        mask2_colored[:, :, 1] = 50
        mask2_region = cv2.bitwise_and(mask2_colored, mask2_colored, mask=mask2)
        h2, w2 = img2.shape[:2]
        overlay[:h2, w1:w1+w2] = cv2.addWeighted(overlay[:h2, w1:w1+w2], 0.85, mask2_region, 0.15, 0)
    
    return overlay


@multiview_3d_bp.route('/api/multiview/gpu-status')
def api_gpu_status():
    """Check GPU status and available models"""
    import torch
    
    info = {
        'cuda_available': torch.cuda.is_available(),
        'cuda_version': torch.version.cuda if torch.cuda.is_available() else None,
        'pytorch_version': torch.__version__,
    }
    
    if torch.cuda.is_available():
        info['gpu_name'] = torch.cuda.get_device_name(0)
        info['gpu_memory_gb'] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 2)
        info['gpu_memory_allocated_gb'] = round(torch.cuda.memory_allocated(0) / 1e9, 2)
    
    # Check if SAM is loaded
    try:
        from basebuddy.routes.plant_tracking import get_sam_predictor
        predictor = get_sam_predictor()
        if predictor is not None:
            device = next(predictor.model.parameters()).device
            info['sam_loaded'] = True
            info['sam_device'] = str(device)
        else:
            info['sam_loaded'] = False
    except Exception as e:
        info['sam_error'] = str(e)
    
    # Check DUSt3R availability
    try:
        from basebuddy.modules.multiview.dust3r_wrapper import check_dust3r_available
        info['dust3r'] = check_dust3r_available()
    except Exception as e:
        info['dust3r'] = {'available': False, 'error': str(e)}
    
    # Recommendations based on setup
    recommendations = []
    if not info['cuda_available']:
        recommendations.append("Install CUDA-enabled PyTorch for 10-50x faster inference")
    if not info.get('dust3r', {}).get('available'):
        recommendations.append("Install DUSt3R for state-of-the-art sparse-view 3D: github.com/naver/dust3r")
    if info['cuda_available'] and info.get('sam_device') == 'cpu':
        recommendations.append("SAM is on CPU but GPU is available - restart app")
    
    info['recommendations'] = recommendations
    
    return jsonify(info)


@multiview_3d_bp.route('/api/multiview/reconstruct-dust3r', methods=['POST'])
def api_reconstruct_dust3r():
    """
    3D reconstruction using DUSt3R (state-of-the-art 2024).
    
    Works with as few as 2 images, no calibration needed.
    """
    try:
        from basebuddy.modules.multiview.dust3r_wrapper import DUSt3RReconstructor, DUST3R_AVAILABLE
        from basebuddy.modules.state import grabbers
        
        if not DUST3R_AVAILABLE:
            return jsonify({
                'ok': False,
                'error': 'DUSt3R not installed',
                'install_hint': (
                    "DUSt3R should be at ~/Projects/dust3r\n"
                    "If missing: git clone https://github.com/naver/dust3r ~/Projects/dust3r\n"
                    "Then restart the app."
                )
            }), 400
        
        data = request.json
        camera_ids = [int(c) for c in data.get('camera_ids', [])]
        use_masks = data.get('use_masks', True)
        
        if len(camera_ids) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Capture frames and apply masks
        logger.info(f"Capturing frames from {len(camera_ids)} cameras for DUSt3R...")
        images = []
        masks_used = []
        for cam_id in camera_ids:
            grabber = grabbers.get(cam_id)
            if grabber:
                frame, _ = grabber.get_latest_frame()
                if frame is not None:
                    # Combined mask (exclude regions + optional seg mask)
                    mask = get_registration_mask(cam_id, frame.shape[:2], use_seg_mask=use_masks)
                    if mask is not None:
                        # Darken masked-out areas instead of black (helps DUSt3R context)
                        masked_frame = frame.copy()
                        masked_frame[mask == 0] = (masked_frame[mask == 0] * 0.3).astype(np.uint8)
                        images.append(masked_frame)
                        masks_used.append(cam_id)
                        logger.info(f"   Camera {cam_id}: {frame.shape} (with mask)")
                    else:
                        images.append(frame)
                        logger.info(f"   Camera {cam_id}: {frame.shape} (no mask)")
        
        if len(images) < 2:
            return jsonify({'ok': False, 'error': 'Could not capture enough frames'}), 400
        
        # Run DUSt3R
        logger.info("Running DUSt3R reconstruction...")
        reconstructor = DUSt3RReconstructor()
        result = reconstructor.reconstruct_from_images(images)
        
        if not result['success']:
            return jsonify(result), 500
        
        # Save point cloud
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f"dust3r_reconstruction_{timestamp}.ply"
        output_path = os.path.join(RECONSTRUCTIONS_DIR, output_filename)
        
        reconstructor.save_point_cloud(result, output_path)

        # Metadata so the gallery can list this reconstruction.
        metadata = {
            'timestamp': timestamp,
            'camera_ids': camera_ids,
            'method': 'dust3r',
            'used_masks': bool(masks_used),
            'num_points': result['num_points'],
            'ply_file': output_filename,
        }
        with open(os.path.join(RECONSTRUCTIONS_DIR, f"dust3r_reconstruction_{timestamp}.json"), 'w') as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"DUSt3R complete: {result['num_points']} points")
        
        return jsonify({
            'ok': True,
            'method': 'DUSt3R',
            'num_points': result['num_points'],
            'num_cameras': len(result['cameras']),
            'masks_used': masks_used,
            'ply_file': output_filename,
            'download_url': f'/api/multiview/download/{output_filename}'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/info')
def api_multiview_info():
    """Get information about multiview 3D reconstruction feature"""
    return jsonify({
        'ok': True,
        'info': {
            'description': 'Multiview 3D plant reconstruction from synchronized camera views',
            'calibration_required': False,  # Now supports fully automatic mode!
            'recommended_cameras': 4,
            'minimum_cameras': 2,
            'calibration_methods': ['checkerboard', 'auto', 'fully_automatic'],
            'checkerboard_specs': {
                'inner_corners': '9x6 or 8x5',
                'square_size_mm': 25.0,
                'recommended_images': '15-20 from different angles'
            }
        }
    })


@multiview_3d_bp.route('/api/multiview/cameras')
def api_get_cameras():
    """
    Get list of cameras with timelapse images (preferred source for 3D reconstruction).
    
    Uses timelapse stills which are already captured and time-stamped,
    rather than trying to access live grabbers directly.
    """
    try:
        from basebuddy.modules.multiview.sync import MultiviewFrameSync
        from basebuddy.modules.multiview.registration_regions import load_regions
        from basebuddy.modules.state import grabbers
        
        sync = MultiviewFrameSync()
        
        # Get cameras that have timelapse images
        cameras = sync.get_cameras_with_timelapse()
        
        # Also check for live grabbers and masks
        for cam in cameras:
            cam_id = cam['id']
            grabber = grabbers.get(cam_id)
            cam['has_live_feed'] = grabber is not None
            
            # Check for saved segmentation mask
            mask_path = os.path.join(MULTIVIEW_DIR, "masks", f"camera_{cam_id}_mask.png")
            cam['has_mask'] = os.path.exists(mask_path)

            # Check for registration regions (include/exclude boxes)
            regions = load_regions(cam_id)
            cam['has_regions'] = bool(regions['exclude'] or regions['include'])
            if cam['has_mask']:
                # Get mask thumbnail URL
                cam['mask_url'] = f"/api/multiview/mask-preview/{cam_id}"
            
            # Also check plant tracking masks as fallback
            if not cam['has_mask']:
                pt_mask_dir = os.path.join(_BASE_DIR, f"plant_segmentation_results/camera_{cam_id}/masks")
                if os.path.exists(pt_mask_dir):
                    mask_files = glob.glob(os.path.join(pt_mask_dir, "*_mask.png"))
                    if mask_files:
                        cam['has_mask'] = True
                        cam['mask_source'] = 'plant_tracking'
            
            # Try to get a live thumbnail if no timelapse thumbnail
            if grabber and not cam.get('thumbnail_url'):
                try:
                    frame, ts = grabber.get_latest_frame()
                    if frame is not None:
                        thumb_dir = os.path.join(MULTIVIEW_DIR, "thumbnails")
                        Path(thumb_dir).mkdir(exist_ok=True)
                        thumb_path = os.path.join(thumb_dir, f"cam_{cam_id}_live.jpg")
                        
                        h, w = frame.shape[:2]
                        scale = 320 / max(w, 1)
                        thumb = cv2.resize(frame, (320, int(h * scale)))
                        cv2.imwrite(thumb_path, thumb)
                        cam['thumbnail_url'] = f"/api/multiview/thumbnail/{cam_id}_live"
                except Exception as e:
                    logger.info(f"[Multiview] Could not get live thumbnail for camera {cam_id}: {e}")
        
        logger.info(f"[Multiview] Found {len(cameras)} cameras with timelapse images")
        
        return jsonify({
            'ok': True,
            'cameras': cameras,
            'total': len(cameras),
            'source': 'timelapse'
        })
        
    except Exception as e:
        import traceback
        logger.info(f"[Multiview] Error in api_get_cameras: {e}")
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/cameras/synced')
def api_get_synced_frames():
    """
    Get time-synchronized frames across all cameras.
    
    Finds the most recent time where all selected cameras have images,
    and returns the nearest frame from each camera.
    """
    try:
        from basebuddy.modules.multiview.sync import MultiviewFrameSync
        
        camera_ids = request.args.getlist('camera_ids', type=int)
        max_time_diff = request.args.get('max_time_diff', type=float, default=60.0)
        
        if not camera_ids:
            # Get all cameras with timelapse
            sync = MultiviewFrameSync(max_time_diff)
            all_cams = sync.get_cameras_with_timelapse()
            camera_ids = [c['id'] for c in all_cams]
        
        if len(camera_ids) < 2:
            return jsonify({
                'ok': False,
                'error': 'Need at least 2 cameras for synchronization'
            }), 400
        
        sync = MultiviewFrameSync(max_time_diff)
        synced = sync.find_synced_frames(camera_ids)
        
        # Format response
        result = {}
        for cam_id, info in synced.items():
            result[cam_id] = {
                'path': info['path'],
                'timestamp': info['timestamp'].isoformat(),
                'time_diff_seconds': info['time_diff_seconds'],
                'url': f"/stills/camera_{cam_id}/{os.path.basename(info['path'])}"
            }
        
        return jsonify({
            'ok': True,
            'synced_frames': result,
            'num_cameras': len(result),
            'reference_time': synced[camera_ids[0]]['timestamp'].isoformat() if synced else None
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/live-feed/<int:camera_id>')
def api_get_live_frame(camera_id):
    """Get a live frame from a camera (MJPEG single frame)"""
    try:
        from basebuddy.modules.state import grabbers
        
        grabber = grabbers.get(camera_id)
        if not grabber:
            return jsonify({'ok': False, 'error': f'Camera {camera_id} not available'}), 404
        
        frame, ts = grabber.get_latest_frame()
        if frame is None:
            return jsonify({'ok': False, 'error': 'No frame available'}), 500
        
        # Encode as JPEG
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        
        return send_file(
            io.BytesIO(buffer),
            mimetype='image/jpeg'
        )
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/thumbnail/<camera_id>')
def api_get_camera_thumbnail(camera_id):
    """Get thumbnail image for a camera"""
    thumb_path = os.path.join(MULTIVIEW_DIR, "thumbnails", f"cam_{camera_id}.jpg")
    if os.path.exists(thumb_path):
        return send_file(thumb_path, mimetype='image/jpeg')
    return jsonify({'ok': False, 'error': 'Thumbnail not found'}), 404


@multiview_3d_bp.route('/api/multiview/mask-preview/<int:camera_id>')
def api_get_mask_preview(camera_id):
    """Get a preview of the segmentation mask overlaid on the camera image"""
    try:
        from basebuddy.modules.state import grabbers
        
        # Get the mask
        mask_path = os.path.join(MULTIVIEW_DIR, "masks", f"camera_{camera_id}_mask.png")
        
        if not os.path.exists(mask_path):
            # Try plant tracking mask
            mask_dir = os.path.join(_BASE_DIR, f"plant_segmentation_results/camera_{camera_id}/masks")
            if os.path.exists(mask_dir):
                mask_files = sorted(glob.glob(os.path.join(mask_dir, "*_mask.png")), reverse=True)
                if mask_files:
                    mask_path = mask_files[0]
        
        if not os.path.exists(mask_path):
            return jsonify({'ok': False, 'error': 'No mask found'}), 404
        
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        
        # Try to get camera frame for overlay
        frame = None
        grabber = grabbers.get(camera_id)
        if grabber:
            frame, _ = grabber.get_latest_frame()
        
        if frame is None:
            # Use timelapse image
            timelapse_dir = os.path.join(_REPO, "stills", f"camera_{camera_id}")
            if os.path.exists(timelapse_dir):
                image_files = sorted(glob.glob(os.path.join(timelapse_dir, "*.jpg")), reverse=True)
                if image_files:
                    frame = cv2.imread(image_files[0])
        
        if frame is not None:
            # Resize mask to match frame
            if mask.shape[:2] != frame.shape[:2]:
                mask = cv2.resize(mask, (frame.shape[1], frame.shape[0]))
            
            # Create overlay: green tint on masked area
            overlay = frame.copy()
            mask_color = np.zeros_like(frame)
            mask_color[:, :, 1] = 100  # Green channel
            overlay[mask > 127] = cv2.addWeighted(frame, 0.6, mask_color, 0.4, 0)[mask > 127]
            
            # Draw mask contour
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, contours, -1, (0, 255, 0), 2)
            
            _, buffer = cv2.imencode('.jpg', overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
        else:
            # Just return the mask as grayscale
            _, buffer = cv2.imencode('.jpg', mask)
        
        return send_file(io.BytesIO(buffer), mimetype='image/jpeg')
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/delete-mask/<int:camera_id>', methods=['DELETE'])
def api_delete_mask(camera_id):
    """Delete a saved mask for a camera"""
    try:
        mask_path = os.path.join(MULTIVIEW_DIR, "masks", f"camera_{camera_id}_mask.png")
        if os.path.exists(mask_path):
            os.remove(mask_path)
            return jsonify({'ok': True, 'message': f'Mask deleted for camera {camera_id}'})
        return jsonify({'ok': False, 'error': 'No mask found'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/fully-automatic', methods=['POST'])
def api_fully_automatic():
    """
    Fully automatic 3D reconstruction using live camera frames.
    
    This mode:
    1. Captures live frames from selected cameras
    2. Uses state-of-the-art feature matching (hybrid SIFT/LoFTR)
    3. Estimates camera intrinsics from image dimensions
    4. Auto-calibrates camera positions
    5. Generates 3D reconstruction
    
    Best for: Quick results, plant growth tracking
    """
    try:
        from basebuddy.modules.multiview.matcher import FeatureMatcher, MatchingMethod
        from basebuddy.modules.multiview.calibration import AutoCalibrator, MultiviewCalibration
        from basebuddy.modules.multiview.reconstruction import MultiviewReconstructor
        
        data = request.json
        camera_ids = data.get('camera_ids', [])
        use_plant_masks = data.get('use_plant_masks', True)
        
        if len(camera_ids) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Convert to int
        camera_ids = [int(c) for c in camera_ids]
        
        logger.info(f"Fully automatic 3D reconstruction with cameras: {camera_ids}")
        
        # Step 1: Get live frames from cameras
        logger.info("   Step 1/4: Capturing live frames...")
        from basebuddy.modules.state import grabbers
        synchronized_images = {}
        failed_cameras = []
        
        for cam_id in camera_ids:
            grabber = grabbers.get(cam_id)
            if grabber is None:
                logger.info(f"      Camera {cam_id}: no grabber available")
                failed_cameras.append(f"{cam_id} (not running)")
                continue
            
            try:
                frame, ts = grabber.get_latest_frame()
                if frame is not None:
                    synchronized_images[str(cam_id)] = [frame]
                    logger.info(f"      Camera {cam_id}: captured frame {frame.shape}")
                else:
                    logger.info(f"      Camera {cam_id}: no frame available")
                    failed_cameras.append(f"{cam_id} (no frame)")
            except Exception as e:
                logger.info(f"      Camera {cam_id}: error - {e}")
                failed_cameras.append(f"{cam_id} ({e})")
        
        if len(synchronized_images) < 2:
            return jsonify({
                'ok': False,
                'error': f'Need at least 2 cameras with frames. Only got {len(synchronized_images)}. Failed: {", ".join(failed_cameras)}'
            }), 400
        
        # Use the cameras we successfully captured from
        camera_ids = [int(k) for k in synchronized_images.keys()]
        logger.info(f"      Captured frames from {len(synchronized_images)} cameras: {camera_ids}")
        
        # Combined registration masks: user exclude/include regions always
        # apply; segmentation masks apply when use_plant_masks is on.
        masks = {}
        masks_loaded = []
        for cam_id in camera_ids:
            cam_key = str(cam_id)
            if cam_key in synchronized_images and synchronized_images[cam_key]:
                img = synchronized_images[cam_key][0]
                mask = get_registration_mask(cam_id, img.shape[:2], use_seg_mask=use_plant_masks)
                if mask is not None:
                    masks[cam_key] = [mask]
                    masks_loaded.append(cam_id)

        if masks_loaded:
            logger.info(f"      Loaded masks for cameras: {masks_loaded}")
        else:
            masks = None
            logger.info(f"      No masks found - using full images")
        
        # Step 2: Feature matching and quality assessment
        logger.info("   Step 2/4: Running state-of-the-art feature matching...")
        matcher = FeatureMatcher(MatchingMethod.HYBRID)
        
        # Test match quality between first two cameras
        cam_keys = list(synchronized_images.keys())
        img1 = synchronized_images[cam_keys[0]][0]
        img2 = synchronized_images[cam_keys[1]][0]
        mask1 = masks.get(cam_keys[0], [None])[0] if masks else None
        mask2 = masks.get(cam_keys[1], [None])[0] if masks else None
        
        match_result = matcher.match(img1, img2, mask1, mask2)
        logger.info(f"      Match quality: {np.sum(match_result.inlier_mask)} inliers, method: {match_result.method}")
        
        # Save match visualization
        viz = matcher.visualize_matches(img1, img2, match_result)
        viz_path = os.path.join(MULTIVIEW_DIR, "latest_matches.jpg")
        cv2.imwrite(viz_path, viz)
        
        # Step 3: Estimate intrinsics and calibrate
        logger.info("   Step 3/4: Calibrating cameras...")
        auto_calibrator = AutoCalibrator(CALIBRATION_DIR)
        calib_manager = MultiviewCalibration(CALIBRATION_DIR)
        
        for cam_id in camera_ids:
            cam_key = str(cam_id)
            if cam_key in synchronized_images and synchronized_images[cam_key]:
                img = synchronized_images[cam_key][0]
                K = auto_calibrator.estimate_intrinsics_from_image(img)
                
                estimated_calib = {
                    'ok': True,
                    'method': 'estimated_from_image_dimensions',
                    'camera_matrix': K.tolist(),
                    'distortion_coefficients': [[0, 0, 0, 0, 0]],
                    'image_size': (img.shape[1], img.shape[0]),
                    'calibration_date': datetime.now().isoformat()
                }
                calib_manager.save_intrinsic_calibration(cam_key, estimated_calib)
        
        # Auto-calibrate camera positions
        logger.info(f"      Calibrating camera pair: {[str(c) for c in camera_ids]}")
        try:
            multiview_result = auto_calibrator.auto_calibrate_multiview(
                [str(c) for c in camera_ids],
                synchronized_images,
                masks
            )
            logger.info(f"      Calibration result: success={multiview_result.get('success')}, pairs={multiview_result.get('num_calibrated_pairs', 0)}")
        except Exception as calib_error:
            import traceback
            traceback.print_exc()
            return jsonify({
                'ok': False,
                'error': f"Calibration exception: {str(calib_error)}",
                'match_visualization': '/api/multiview/latest-matches'
            }), 500
        
        if not multiview_result['success']:
            return jsonify({
                'ok': False,
                'error': f"Auto-calibration failed: {multiview_result.get('error', 'Unknown error')}",
                'failed_pairs': multiview_result.get('failed_pairs', []),
                'match_visualization': '/api/multiview/latest-matches'
            }), 500
        
        calibration_quality = "good" if multiview_result.get('num_calibrated_pairs', 0) >= len(camera_ids) - 1 else "partial"
        
        # Step 4: Generate 3D reconstruction
        logger.info("   Step 4/4: Generating 3D reconstruction...")
        try:
            # Use the calibration result directly instead of reloading from file
            # This ensures we use the calibration we just computed
            logger.info(f"      Using calibration for cameras: {multiview_result.get('camera_ids', [])}")
            reconstructor = MultiviewReconstructor(multiview_result, CALIBRATION_DIR)
        except Exception as load_error:
            import traceback
            traceback.print_exc()
            return jsonify({
                'ok': False,
                'error': f"Failed to initialize reconstructor: {str(load_error)}",
                'match_visualization': '/api/multiview/latest-matches'
            }), 500
        
        images_for_recon = {k: v[0] for k, v in synchronized_images.items()}
        masks_for_recon = {k: v[0] for k, v in masks.items()} if masks else None
        logger.info(f"      Reconstructing from {len(images_for_recon)} images with {len(masks_for_recon) if masks_for_recon else 0} masks")
        
        try:
            result = reconstructor.reconstruct_from_features(images_for_recon, masks_for_recon)
            logger.info(f"      Reconstruction result: success={result.get('success')}, points={result.get('num_points', 0)}")
        except Exception as recon_error:
            import traceback
            traceback.print_exc()
            return jsonify({
                'ok': False,
                'error': f"Reconstruction exception: {str(recon_error)}",
                'match_visualization': '/api/multiview/latest-matches'
            }), 500
        
        if not result['success']:
            return jsonify({
                'ok': False,
                'error': f"Reconstruction failed: {result.get('error', 'Unknown error')}",
                'match_visualization': '/api/multiview/latest-matches'
            }), 500
        
        # Save point cloud
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f"auto_reconstruction_{timestamp}.ply"
        output_path = os.path.join(RECONSTRUCTIONS_DIR, output_filename)
        
        reconstructor.save_point_cloud_ply(
            result['points_3d'],
            result['colors'],
            output_path
        )
        
        # Save metadata
        metadata = {
            'timestamp': timestamp,
            'camera_ids': camera_ids,
            'method': 'fully_automatic',
            'matching_method': match_result.method,
            'num_matches': len(match_result.points1),
            'num_inliers': int(np.sum(match_result.inlier_mask)),
            'used_masks': use_plant_masks,
            'num_points': result['num_points'],
            'bounds': result.get('bounds', {}),
            'calibration_quality': calibration_quality,
            'ply_file': output_filename
        }
        
        metadata_path = os.path.join(RECONSTRUCTIONS_DIR, f"auto_reconstruction_{timestamp}.json")
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"Fully automatic reconstruction complete: {result['num_points']} points")
        
        return jsonify({
            'ok': True,
            'reconstruction_id': timestamp,
            'num_points': result['num_points'],
            'camera_ids': camera_ids,
            'calibration_quality': calibration_quality,
            'matching_stats': {
                'method': match_result.method,
                'num_matches': len(match_result.points1),
                'num_inliers': int(np.sum(match_result.inlier_mask))
            },
            'bounds': result.get('bounds', {}),
            'ply_file': output_filename,
            'download_url': f'/api/multiview/download/{output_filename}',
            'match_visualization': '/api/multiview/latest-matches'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/latest-matches')
def api_get_latest_matches():
    """Get the latest feature match visualization"""
    viz_path = os.path.join(MULTIVIEW_DIR, "latest_matches.jpg")
    if os.path.exists(viz_path):
        return send_file(viz_path, mimetype='image/jpeg')
    return jsonify({'ok': False, 'error': 'No match visualization available'}), 404


@multiview_3d_bp.route('/api/multiview/test-matching', methods=['POST'])
def api_test_matching():
    """
    Test feature matching quality between selected cameras.
    
    Uses live camera frames and plant segmentation masks for focused matching.
    """
    try:
        from basebuddy.modules.multiview.matcher import FeatureMatcher, MatchingMethod
        from basebuddy.modules.state import grabbers
        from datetime import datetime
        
        data = request.json
        camera_ids = [int(c) for c in data.get('camera_ids', [])]
        use_masks = data.get('use_masks', True)  # Use plant segmentation masks by default
        
        if len(camera_ids) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Get live frames from camera grabbers
        frames = {}
        masks = {}
        frame_info = {}
        failed_cameras = []
        
        for cam_id in camera_ids:  # Try all selected cameras until we get 2
            if len(frames) >= 2:
                break
                
            grabber = grabbers.get(cam_id)
            if grabber is None:
                logger.info(f"[Multiview] No grabber for camera {cam_id}")
                failed_cameras.append(f"{cam_id} (not running)")
                continue
            
            try:
                frame, ts = grabber.get_latest_frame()
                if frame is not None:
                    frames[cam_id] = frame
                    frame_info[cam_id] = {
                        'source': 'live',
                        'timestamp': datetime.fromtimestamp(ts) if ts else datetime.now()
                    }
                    logger.info(f"[Multiview] Got live frame for camera {cam_id}, shape: {frame.shape}")
                    
                    # Combined mask: exclude/include regions + optional seg mask
                    mask = get_registration_mask(cam_id, frame.shape[:2], use_seg_mask=use_masks)
                    if mask is not None:
                        masks[cam_id] = mask
                        logger.info(f"[Multiview] Loaded registration mask for camera {cam_id}")
                else:
                    logger.info(f"[Multiview] No frame from camera {cam_id}")
                    failed_cameras.append(f"{cam_id} (no frame)")
            except Exception as e:
                logger.info(f"[Multiview] Error getting frame from camera {cam_id}: {e}")
                failed_cameras.append(f"{cam_id} ({e})")
        
        if len(frames) < 2:
            # Return partial success with info about what failed
            return jsonify({
                'ok': False,
                'partial': True,
                'frames_captured': len(frames),
                'failed_cameras': failed_cameras,
                'error': f'Need 2 cameras with frames. Got {len(frames)}. Try selecting different cameras.',
                'hint': 'Check the Camera Wall to see which cameras are streaming.'
            }), 400
        
        # Test matching between first two cameras
        cam_keys = list(frames.keys())
        img1 = frames[cam_keys[0]]
        img2 = frames[cam_keys[1]]
        
        # Get masks if available
        mask1 = masks.get(cam_keys[0])
        mask2 = masks.get(cam_keys[1])
        
        # If no masks loaded, fall back to plant color masks (only when the
        # caller asked for masked matching).
        if use_masks and mask1 is None:
            mask1 = create_plant_color_mask(img1)
        if use_masks and mask2 is None:
            mask2 = create_plant_color_mask(img2)
        
        matcher = FeatureMatcher(MatchingMethod.HYBRID)
        result = matcher.match(img1, img2, mask1, mask2)
        
        # Generate visualization with masks overlaid
        viz = matcher.visualize_matches(img1, img2, result)
        
        # Add mask overlay to visualization if masks were used
        if mask1 is not None or mask2 is not None:
            viz = add_mask_overlay_to_viz(viz, img1, img2, mask1, mask2)
        viz_path = os.path.join(MULTIVIEW_DIR, "test_matches.jpg")
        
        # Ensure directory exists and save
        Path(MULTIVIEW_DIR).mkdir(parents=True, exist_ok=True)
        success = cv2.imwrite(viz_path, viz)
        logger.info(f"[Multiview] Saved visualization to {viz_path}: {success}, size: {viz.shape if viz is not None else 'None'}")
        
        # Quality assessment
        num_inliers = int(np.sum(result.inlier_mask))
        inlier_ratio = num_inliers / max(1, len(result.inlier_mask))
        
        quality = "excellent" if num_inliers > 200 and inlier_ratio > 0.5 else \
                  "good" if num_inliers > 50 and inlier_ratio > 0.3 else \
                  "acceptable" if num_inliers > 20 else "poor"
        
        recommendations = []
        if num_inliers < 20:
            recommendations.append("Very few matches found. Cameras may not have overlapping views.")
        if inlier_ratio < 0.3:
            recommendations.append("Low inlier ratio. Consider better lighting or more textured subjects.")
        if num_inliers > 200:
            recommendations.append("Excellent match quality! Ready for 3D reconstruction.")
        
        # Get camera timestamps for info
        cam1_ts = frame_info[cam_keys[0]]['timestamp'].isoformat() if cam_keys[0] in frame_info else 'N/A'
        cam2_ts = frame_info[cam_keys[1]]['timestamp'].isoformat() if cam_keys[1] in frame_info else 'N/A'
        
        # Ensure the visualization was saved
        logger.info(f"[Multiview] Visualization saved to: {viz_path}, exists: {os.path.exists(viz_path)}")
        
        # Track which cameras had masks applied (for UI)
        masks_used = []
        masks_used_names = []
        if mask1 is not None and cam_keys[0] in masks:  # Only count if it was from our mask system, not color fallback
            masks_used.append(cam_keys[0])
            masks_used_names.append(f"Camera {cam_keys[0]}")
        if mask2 is not None and cam_keys[1] in masks:
            masks_used.append(cam_keys[1])
            masks_used_names.append(f"Camera {cam_keys[1]}")
        
        # Add failed camera info to recommendations
        if failed_cameras:
            recommendations.append(f"Note: {len(failed_cameras)} camera(s) had no frames available.")
        
        return jsonify({
            'ok': True,
            'cameras_tested': [cam_keys[0], cam_keys[1]],
            'source': 'live',
            'masks_used': masks_used,
            'masks_used_names': masks_used_names,
            'camera_timestamps': {
                str(cam_keys[0]): cam1_ts,
                str(cam_keys[1]): cam2_ts
            },
            'method': result.method,
            'num_matches': len(result.points1),
            'num_inliers': num_inliers,
            'inlier_ratio': round(inlier_ratio, 3),
            'quality': quality,
            'processing_time_ms': round(result.processing_time_ms, 1),
            'recommendations': recommendations,
            'failed_cameras': failed_cameras,
            'visualization_url': f'/api/multiview/test-matches?t={int(datetime.now().timestamp())}'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/test-matches')
def api_get_test_matches():
    """Get the test matching visualization"""
    viz_path = os.path.join(MULTIVIEW_DIR, "test_matches.jpg")
    logger.info(f"[Multiview] Serving test_matches.jpg from {viz_path}, exists: {os.path.exists(viz_path)}")
    if os.path.exists(viz_path):
        return send_file(viz_path, mimetype='image/jpeg')
    return jsonify({'ok': False, 'error': f'No test visualization available at {viz_path}'}), 500


# ============ INTERACTIVE SEGMENTATION API ============

# Store interactive session state
_interactive_sessions = {}

@multiview_3d_bp.route('/api/multiview/segment/init', methods=['POST'])
def api_init_interactive_segment():
    """
    Initialize interactive segmentation session for a camera.
    
    Gets a live frame and prepares SAM for interactive annotation.
    """
    try:
        from basebuddy.modules.state import grabbers
        
        data = request.json
        camera_id = int(data.get('camera_id'))
        
        grabber = grabbers.get(camera_id)
        if grabber is None:
            return jsonify({'ok': False, 'error': f'Camera {camera_id} not running'}), 400
        
        frame, ts = grabber.get_latest_frame()
        if frame is None:
            return jsonify({'ok': False, 'error': 'No frame available'}), 400
        
        # Create session
        session_id = f"cam_{camera_id}_{int(datetime.now().timestamp())}"
        
        # Store frame and initialize session
        session_path = os.path.join(MULTIVIEW_DIR, "sessions", session_id)
        Path(session_path).mkdir(parents=True, exist_ok=True)
        
        frame_path = os.path.join(session_path, "frame.jpg")
        cv2.imwrite(frame_path, frame)
        
        _interactive_sessions[session_id] = {
            'camera_id': camera_id,
            'frame_path': frame_path,
            'frame_shape': frame.shape[:2],
            'points': [],
            'labels': [],
            'mask': None
        }
        
        logger.info(f"[Multiview] Created interactive session {session_id}")
        
        return jsonify({
            'ok': True,
            'session_id': session_id,
            'frame_url': f'/api/multiview/segment/frame/{session_id}',
            'width': frame.shape[1],
            'height': frame.shape[0]
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/segment/frame/<session_id>')
def api_get_segment_frame(session_id):
    """Get the frame image for a segmentation session"""
    session = _interactive_sessions.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    if os.path.exists(session['frame_path']):
        return send_file(session['frame_path'], mimetype='image/jpeg')
    return jsonify({'ok': False, 'error': 'Frame not found'}), 404


@multiview_3d_bp.route('/api/multiview/segment/click', methods=['POST'])
def api_segment_click():
    """
    Add a click point and update segmentation mask.
    
    Left click (is_foreground=true) = include this region
    Right click (is_foreground=false) = exclude this region
    """
    try:
        data = request.json
        session_id = data.get('session_id')
        x = int(data.get('x'))
        y = int(data.get('y'))
        is_foreground = data.get('is_foreground', True)
        
        session = _interactive_sessions.get(session_id)
        if not session:
            return jsonify({'ok': False, 'error': 'Session not found'}), 404
        
        # Add point
        session['points'].append([x, y])
        session['labels'].append(1 if is_foreground else 0)
        
        # Run SAM to update mask
        mask, metrics = run_sam_segmentation(session)
        session['mask'] = mask
        
        # Save mask visualization
        mask_viz_path = os.path.join(os.path.dirname(session['frame_path']), "mask.png")
        cv2.imwrite(mask_viz_path, mask)
        
        return jsonify({
            'ok': True,
            'num_points': len(session['points']),
            'fg_points': sum(session['labels']),
            'bg_points': len(session['labels']) - sum(session['labels']),
            'mask_url': f'/api/multiview/segment/mask/{session_id}?t={int(datetime.now().timestamp())}',
            'coverage': metrics.get('coverage', 0),
            'processing_time_ms': metrics.get('time_ms', 0)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/segment/mask/<session_id>')
def api_get_segment_mask(session_id):
    """Get the current segmentation mask as a PNG"""
    session = _interactive_sessions.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    mask_path = os.path.join(os.path.dirname(session['frame_path']), "mask.png")
    if os.path.exists(mask_path):
        return send_file(mask_path, mimetype='image/png')
    
    # Return empty mask if none exists yet
    h, w = session['frame_shape']
    empty = np.zeros((h, w), dtype=np.uint8)
    _, buffer = cv2.imencode('.png', empty)
    return send_file(io.BytesIO(buffer), mimetype='image/png')


@multiview_3d_bp.route('/api/multiview/segment/reset', methods=['POST'])
def api_segment_reset():
    """Reset all points in a segmentation session"""
    data = request.json
    session_id = data.get('session_id')
    
    session = _interactive_sessions.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    session['points'] = []
    session['labels'] = []
    session['mask'] = None
    
    return jsonify({'ok': True})


@multiview_3d_bp.route('/api/multiview/segment/save', methods=['POST'])
def api_segment_save():
    """Save the current mask for use in matching"""
    data = request.json
    session_id = data.get('session_id')
    
    session = _interactive_sessions.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    if session['mask'] is None:
        return jsonify({'ok': False, 'error': 'No mask to save'}), 400
    
    # Save mask to persistent location
    camera_id = session['camera_id']
    mask_dir = os.path.join(MULTIVIEW_DIR, "masks")
    Path(mask_dir).mkdir(exist_ok=True)
    
    mask_path = os.path.join(mask_dir, f"camera_{camera_id}_mask.png")
    cv2.imwrite(mask_path, session['mask'])
    
    # Also save the annotation pattern for reuse
    pattern = {
        'points': session['points'],
        'labels': session['labels'],
        'frame_shape': session['frame_shape']
    }
    pattern_path = os.path.join(mask_dir, f"camera_{camera_id}_pattern.json")
    with open(pattern_path, 'w') as f:
        json.dump(pattern, f)
    
    return jsonify({
        'ok': True,
        'mask_path': mask_path,
        'camera_id': camera_id
    })


@multiview_3d_bp.route('/api/multiview/regions/<int:camera_id>', methods=['GET'])
def api_get_registration_regions(camera_id):
    """Get per-camera registration region config (normalized 0-1 boxes)."""
    from basebuddy.modules.multiview.registration_regions import load_regions

    return jsonify({'ok': True, 'camera_id': camera_id, 'regions': load_regions(camera_id)})


@multiview_3d_bp.route('/api/multiview/regions/<int:camera_id>', methods=['POST'])
def api_save_registration_regions(camera_id):
    """
    Save registration regions for a camera.

    Body: {exclude: [[x1,y1,x2,y2], ...], include: [...], use_seg_mask: bool}
    Coordinates are normalized to [0, 1]. Exclude boxes are always removed
    from registration; if include boxes exist, only those areas are used.
    """
    from basebuddy.modules.multiview.registration_regions import save_regions

    data = request.json or {}
    saved = save_regions(camera_id, data)
    logger.info(f"[Multiview] Saved registration regions for camera {camera_id}: "
                f"{len(saved['exclude'])} exclude, {len(saved['include'])} include, "
                f"use_seg_mask={saved['use_seg_mask']}")
    return jsonify({'ok': True, 'camera_id': camera_id, 'regions': saved})


@multiview_3d_bp.route('/api/multiview/regions/<int:camera_id>', methods=['DELETE'])
def api_delete_registration_regions(camera_id):
    """Remove all registration regions for a camera."""
    from basebuddy.modules.multiview.registration_regions import clear_regions

    clear_regions(camera_id)
    return jsonify({'ok': True, 'camera_id': camera_id})


def run_sam_segmentation(session: dict) -> tuple:
    """Run SAM segmentation with current session points"""
    import time
    start = time.time()
    
    from basebuddy.routes.plant_tracking import get_sam_predictor
    
    predictor = get_sam_predictor()
    if predictor is None:
        return np.zeros(session['frame_shape'], dtype=np.uint8), {'error': 'SAM not available'}
    
    # Load frame
    frame = cv2.imread(session['frame_path'])
    if frame is None:
        return np.zeros(session['frame_shape'], dtype=np.uint8), {'error': 'Frame not found'}
    
    # Set image
    predictor.set_image(frame)
    
    # Predict
    point_coords = np.array(session['points'])
    point_labels = np.array(session['labels'])
    
    masks, scores, logits = predictor.predict(
        point_coords=point_coords,
        point_labels=point_labels,
        multimask_output=False
    )
    
    mask = (masks[0] * 255).astype(np.uint8)
    
    # Calculate metrics
    coverage = np.sum(mask > 0) / (mask.shape[0] * mask.shape[1])
    time_ms = (time.time() - start) * 1000
    
    return mask, {
        'coverage': round(coverage, 4),
        'time_ms': round(time_ms, 1)
    }


@multiview_3d_bp.route('/api/multiview/calibration/checkerboard')
def api_get_checkerboard_pattern():
    """Generate a checkerboard pattern for printing"""
    try:
        # Get parameters
        width = request.args.get('width', type=int, default=9)
        height = request.args.get('height', type=int, default=6)
        square_size = request.args.get('square_size', type=int, default=50)  # pixels for display
        
        # Create checkerboard pattern
        board_width = (width + 1) * square_size
        board_height = (height + 1) * square_size
        
        checkerboard = np.zeros((board_height, board_width), dtype=np.uint8)
        
        for i in range(height + 1):
            for j in range(width + 1):
                if (i + j) % 2 == 0:
                    y1 = i * square_size
                    y2 = (i + 1) * square_size
                    x1 = j * square_size
                    x2 = (j + 1) * square_size
                    checkerboard[y1:y2, x1:x2] = 255
        
        # Add margin
        checkerboard = cv2.copyMakeBorder(
            checkerboard, 
            square_size, square_size, square_size, square_size,
            cv2.BORDER_CONSTANT, 
            value=255
        )
        
        # Encode as PNG
        _, buffer = cv2.imencode('.png', checkerboard)
        
        return send_file(
            io.BytesIO(buffer),
            mimetype='image/png',
            as_attachment=True,
            download_name=f'checkerboard_{width}x{height}.png'
        )
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/upload', methods=['POST'])
def api_upload_calibration_images():
    """Upload calibration images for a camera"""
    try:
        from basebuddy.core.upload_safety import allowed_image_extension, resolve_under_dir

        camera_id_raw = request.form.get('camera_id')
        if not camera_id_raw or not str(camera_id_raw).isdigit():
            return jsonify({'ok': False, 'error': 'camera_id must be a positive integer'}), 400

        camera_calib_dir = resolve_under_dir(CALIBRATION_IMAGES_DIR, f"camera_{int(camera_id_raw)}")
        if camera_calib_dir is None:
            return jsonify({'ok': False, 'error': 'Invalid upload path'}), 400
        camera_calib_dir.mkdir(parents=True, exist_ok=True)

        files = request.files.getlist('images')
        if len(files) > 50:
            return jsonify({'ok': False, 'error': 'Max 50 images per upload'}), 400

        saved_files = []
        for file in files:
            if not file or not file.filename:
                continue
            if not allowed_image_extension(file.filename, {'.jpg', '.jpeg', '.png'}):
                continue
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            filename = f"calib_{timestamp}.jpg"
            filepath = camera_calib_dir / filename
            file.save(str(filepath))
            saved_files.append(filename)

        if not saved_files:
            return jsonify({'ok': False, 'error': 'No valid image files (jpg/png) uploaded'}), 400

        return jsonify({
            'ok': True,
            'camera_id': camera_id_raw,
            'num_images': len(saved_files),
            'files': saved_files
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/intrinsic', methods=['POST'])
def api_calibrate_intrinsic():
    """Run intrinsic calibration for a camera"""
    try:
        from basebuddy.modules.multiview import CameraCalibrator, MultiviewCalibration
        
        data = request.json
        camera_id = data.get('camera_id')
        checkerboard_width = data.get('checkerboard_width', 9)
        checkerboard_height = data.get('checkerboard_height', 6)
        square_size_mm = data.get('square_size_mm', 25.0)
        
        if not camera_id:
            return jsonify({'ok': False, 'error': 'camera_id required'}), 400
        
        # Load calibration images
        camera_calib_dir = os.path.join(CALIBRATION_IMAGES_DIR, f"camera_{camera_id}")
        if not os.path.exists(camera_calib_dir):
            return jsonify({'ok': False, 'error': 'No calibration images found'}), 404
        
        image_files = glob.glob(os.path.join(camera_calib_dir, "*.jpg"))
        if len(image_files) < 10:
            return jsonify({
                'ok': False, 
                'error': f'Need at least 10 images, found {len(image_files)}'
            }), 400
        
        # Load images
        images = []
        for img_path in image_files:
            img = cv2.imread(img_path)
            if img is not None:
                images.append(img)
        
        # Calibrate
        calibrator = CameraCalibrator(
            checkerboard_size=(checkerboard_width, checkerboard_height),
            square_size_mm=square_size_mm
        )
        
        result = calibrator.calibrate_camera(images)
        
        if result['success']:
            # Save calibration
            calib_manager = MultiviewCalibration(CALIBRATION_DIR)
            calib_manager.save_intrinsic_calibration(camera_id, result)
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/intrinsic/<camera_id>')
def api_get_intrinsic_calibration(camera_id):
    """Get intrinsic calibration for a camera"""
    try:
        from basebuddy.modules.multiview import MultiviewCalibration
        
        calib_manager = MultiviewCalibration(CALIBRATION_DIR)
        calibration = calib_manager.load_intrinsic_calibration(camera_id)
        
        if calibration:
            return jsonify({'ok': True, 'calibration': calibration})
        else:
            return jsonify({'ok': False, 'error': 'Calibration not found'}), 404
            
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/multiview', methods=['POST'])
def api_calibrate_multiview():
    """Run multiview calibration for multiple cameras"""
    try:
        from basebuddy.modules.multiview import MultiviewCalibration
        
        data = request.json
        camera_ids = data.get('camera_ids', [])
        checkerboard_width = data.get('checkerboard_width', 9)
        checkerboard_height = data.get('checkerboard_height', 6)
        square_size_mm = data.get('square_size_mm', 25.0)
        
        if len(camera_ids) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Load synchronized calibration images for all cameras
        synchronized_images = {}
        
        for cam_id in camera_ids:
            camera_calib_dir = os.path.join(CALIBRATION_IMAGES_DIR, f"camera_{cam_id}")
            if not os.path.exists(camera_calib_dir):
                return jsonify({
                    'ok': False, 
                    'error': f'No calibration images for camera {cam_id}'
                }), 404
            
            image_files = sorted(glob.glob(os.path.join(camera_calib_dir, "*.jpg")))
            images = [cv2.imread(f) for f in image_files if cv2.imread(f) is not None]
            synchronized_images[cam_id] = images
        
        # Verify same number of images (synchronized)
        num_images = len(synchronized_images[camera_ids[0]])
        for cam_id in camera_ids[1:]:
            if len(synchronized_images[cam_id]) != num_images:
                return jsonify({
                    'ok': False,
                    'error': 'Cameras must have same number of synchronized images'
                }), 400
        
        # Run multiview calibration
        calib_manager = MultiviewCalibration(CALIBRATION_DIR)
        result = calib_manager.calibrate_multiview(
            camera_ids,
            synchronized_images,
            checkerboard_size=(checkerboard_width, checkerboard_height),
            square_size_mm=square_size_mm
        )
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/auto', methods=['POST'])
def api_auto_calibrate():
    """
    Auto-calibrate cameras using feature matching (no checkerboard needed for extrinsics!)
    
    This is ideal for inward-facing cameras where showing a checkerboard to all cameras
    simultaneously is impractical.
    
    Note: For best intrinsic calibration, still use checkerboard on each camera individually.
    This method handles EXTRINSIC (relative position) calibration automatically.
    """
    try:
        from basebuddy.modules.multiview.calibration import AutoCalibrator, MultiviewCalibration
        
        data = request.json
        camera_ids = data.get('camera_ids', [])
        use_plant_masks = data.get('use_plant_masks', False)
        
        if len(camera_ids) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Capture synchronized images from cameras
        import basebuddy.modules.state as shared_state
        
        synchronized_images = {}
        masks = {} if use_plant_masks else None
        
        # Capture multiple frames for robustness
        num_captures = 5
        
        for i in range(num_captures):
            for cam_id in camera_ids:
                if cam_id not in shared_state.grabbers:
                    return jsonify({
                        'ok': False, 
                        'error': f'Camera {cam_id} not available'
                    }), 400
                
                grabber = shared_state.grabbers[cam_id]
                frame, _ts = grabber.get_latest_frame()
                
                if frame is None:
                    return jsonify({
                        'ok': False,
                        'error': f'Could not capture from camera {cam_id}'
                    }), 500
                
                if cam_id not in synchronized_images:
                    synchronized_images[cam_id] = []
                synchronized_images[cam_id].append(frame)
                
                # Load plant mask if available and requested
                if use_plant_masks:
                    from basebuddy.modules.config import STILLS_DIR
                    mask_dir = f"plant_segmentation_results/camera_{cam_id}/masks"
                    mask_files = sorted(glob.glob(os.path.join(mask_dir, "*_mask.png")))
                    if mask_files:
                        # Use most recent mask as template
                        mask = cv2.imread(mask_files[-1], cv2.IMREAD_GRAYSCALE)
                        if mask is not None:
                            if cam_id not in masks:
                                masks[cam_id] = []
                            masks[cam_id].append(mask)
            
            # Small delay between captures
            import time
            time.sleep(0.2)
        
        # Run auto-calibration
        auto_calibrator = AutoCalibrator(CALIBRATION_DIR)
        result = auto_calibrator.auto_calibrate_multiview(
            camera_ids,
            synchronized_images,
            masks
        )
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/calibration/auto-test', methods=['POST'])
def api_auto_calibrate_test():
    """
    Test auto-calibration between two cameras and return match visualization
    
    Useful for verifying cameras have enough feature overlap before full calibration
    """
    try:
        from basebuddy.modules.multiview.calibration import AutoCalibrator
        
        data = request.json
        camera1_id = data.get('camera1_id')
        camera2_id = data.get('camera2_id')
        
        if not camera1_id or not camera2_id:
            return jsonify({'ok': False, 'error': 'Need camera1_id and camera2_id'}), 400
        
        # Capture from both cameras
        import basebuddy.modules.state as shared_state
        
        frames = {}
        for cam_id in [camera1_id, camera2_id]:
            if cam_id not in shared_state.grabbers:
                return jsonify({
                    'ok': False,
                    'error': f'Camera {cam_id} not available'
                }), 400
            
            frame, _ts = shared_state.grabbers[cam_id].get_latest_frame()
            if frame is None:
                return jsonify({
                    'ok': False,
                    'error': f'Could not capture from {cam_id}'
                }), 500
            frames[cam_id] = frame
        
        # Find matches
        auto_calibrator = AutoCalibrator(CALIBRATION_DIR)
        pts1, pts2 = auto_calibrator.find_matches(
            frames[camera1_id], 
            frames[camera2_id]
        )
        
        # Create visualization
        viz_img = auto_calibrator.visualize_matches(
            frames[camera1_id],
            frames[camera2_id],
            pts1, pts2,
            max_matches=100
        )
        
        # Save visualization
        viz_path = os.path.join(MULTIVIEW_DIR, "match_test.jpg")
        cv2.imwrite(viz_path, viz_img)
        
        # Estimate pose quality
        result = auto_calibrator.estimate_relative_pose(
            frames[camera1_id],
            frames[camera2_id]
        )
        
        return jsonify({
            'ok': True,
            'num_matches': len(pts1),
            'pose_estimation': {
                'ok': result.get('success', False),
                'num_inliers': result.get('num_inliers', 0),
                'inlier_ratio': result.get('inlier_ratio', 0),
                'quality_score': result.get('quality_score', 0),
                'error': result.get('error', None)
            },
            'visualization_url': f'/api/multiview/match-test-image',
            'recommendation': 'good' if result.get('quality_score', 0) > 0.3 else 
                            'marginal' if result.get('quality_score', 0) > 0.1 else 'poor'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/match-test-image')
def api_get_match_test_image():
    """Get the match test visualization image"""
    viz_path = os.path.join(MULTIVIEW_DIR, "match_test.jpg")
    if os.path.exists(viz_path):
        return send_file(viz_path, mimetype='image/jpeg')
    return jsonify({'ok': False, 'error': 'No test image available'}), 404


@multiview_3d_bp.route('/api/multiview/calibration/status')
def api_calibration_status():
    """Get calibration status for all cameras"""
    try:
        from basebuddy.modules.multiview import MultiviewCalibration
        
        calib_manager = MultiviewCalibration(CALIBRATION_DIR)
        
        # Get all camera calibration directories
        camera_dirs = glob.glob(os.path.join(CALIBRATION_IMAGES_DIR, "camera_*"))
        
        status = {
            'cameras': {},
            'multiview_calibrated': False
        }
        
        for cam_dir in camera_dirs:
            camera_id = os.path.basename(cam_dir).replace('camera_', '')
            num_images = len(glob.glob(os.path.join(cam_dir, "*.jpg")))
            
            intrinsic = calib_manager.load_intrinsic_calibration(camera_id)
            
            status['cameras'][camera_id] = {
                'num_calibration_images': num_images,
                'intrinsic_calibrated': intrinsic is not None,
                'calibration_date': intrinsic.get('calibration_date') if intrinsic else None
            }
        
        # Check multiview calibration
        multiview_calib = calib_manager.load_multiview_calibration()
        if multiview_calib:
            status['multiview_calibrated'] = True
            status['multiview_cameras'] = multiview_calib['camera_ids']
            status['multiview_reference'] = multiview_calib['reference_camera']
        
        return jsonify({'ok': True, 'status': status})
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/reconstruct', methods=['POST'])
def api_reconstruct_3d():
    """Perform 3D reconstruction from synchronized images"""
    try:
        from basebuddy.modules.multiview import MultiviewCalibration, MultiviewReconstructor
        
        data = request.json
        image_sources = data.get('image_sources', {})  # Dict: camera_id -> image path
        use_masks = data.get('use_masks', False)
        method = data.get('method', 'feature')  # 'feature' or 'dense'
        
        if len(image_sources) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400
        
        # Load multiview calibration
        calib_manager = MultiviewCalibration(CALIBRATION_DIR)
        multiview_calib = calib_manager.load_multiview_calibration()
        
        if not multiview_calib:
            return jsonify({
                'ok': False,
                'error': 'Multiview calibration required. Run calibration first.'
            }), 400
        
        # Load images
        images = {}
        masks = {} if use_masks else None
        
        for cam_id, img_path in image_sources.items():
            if not os.path.exists(img_path):
                return jsonify({
                    'ok': False,
                    'error': f'Image not found: {img_path}'
                }), 404
            
            img = cv2.imread(img_path)
            if img is None:
                return jsonify({
                    'ok': False,
                    'error': f'Could not load image: {img_path}'
                }), 500
            
            images[cam_id] = img
            
            # Load mask if requested
            if use_masks:
                # Try to find corresponding mask
                mask_path = img_path.replace('.jpg', '_mask.png')
                mask_path = mask_path.replace('/stills/', '/plant_segmentation_results/')
                mask_path = mask_path.replace(f'{cam_id}/', f'{cam_id}/masks/')
                
                if os.path.exists(mask_path):
                    mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
                    masks[cam_id] = mask
        
        # Create reconstructor
        reconstructor = MultiviewReconstructor(multiview_calib)
        
        # Run reconstruction
        if method == 'feature':
            result = reconstructor.reconstruct_from_features(images, masks)
        elif method == 'dense' and len(images) == 2:
            # Dense only works for stereo pairs
            cam_ids = list(images.keys())
            result = reconstructor.dense_reconstruction_stereo(
                images[cam_ids[0]],
                images[cam_ids[1]],
                cam_ids[0],
                cam_ids[1],
                masks.get(cam_ids[0]) if masks else None,
                masks.get(cam_ids[1]) if masks else None
            )
        else:
            return jsonify({
                'ok': False,
                'error': 'Invalid method or camera configuration'
            }), 400
        
        if not result['success']:
            return jsonify(result), 500
        
        # Save point cloud
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f"reconstruction_{timestamp}.ply"
        output_path = os.path.join(RECONSTRUCTIONS_DIR, output_filename)
        
        reconstructor.save_point_cloud_ply(
            result['points_3d'],
            result['colors'],
            output_path
        )
        
        # Also save metadata
        metadata = {
            'timestamp': timestamp,
            'camera_ids': list(images.keys()),
            'method': method,
            'used_masks': use_masks,
            'num_points': result['num_points'],
            'bounds': result.get('bounds', {}),
            'ply_file': output_filename
        }
        
        metadata_path = os.path.join(RECONSTRUCTIONS_DIR, f"reconstruction_{timestamp}.json")
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return jsonify({
            'ok': True,
            'reconstruction_id': timestamp,
            'num_points': result['num_points'],
            'bounds': result.get('bounds', {}),
            'download_url': f'/api/multiview/download/{output_filename}'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


def _normalize_reconstruction_meta(meta_path: str) -> dict:
    """Normalize new and legacy metadata JSON files to the META schema the
    frontend consumes."""
    with open(meta_path, 'r') as f:
        meta = json.load(f)

    recon_id = meta.get('id') or os.path.splitext(os.path.basename(meta_path))[0]
    timestamp = meta.get('timestamp', '')
    # Legacy records store '%Y%m%d_%H%M%S'; convert to ISO.
    if timestamp and 'T' not in timestamp:
        try:
            timestamp = datetime.strptime(timestamp, '%Y%m%d_%H%M%S').isoformat()
        except ValueError:
            pass

    ply_file = meta.get('ply_file', '')
    ply_path = os.path.join(RECONSTRUCTIONS_DIR, ply_file) if ply_file else ''
    size_bytes = os.path.getsize(ply_path) if ply_path and os.path.exists(ply_path) else 0

    return {
        'id': recon_id,
        'timestamp': timestamp,
        'engine': meta.get('engine') or meta.get('method', 'unknown'),
        'camera_ids': meta.get('camera_ids', []),
        'num_points': meta.get('num_points', 0),
        'ply_file': ply_file,
        'ply_url': f'/api/multiview/cloud/{ply_file}' if ply_file else None,
        'download_url': f'/api/multiview/download/{ply_file}' if ply_file else None,
        'size_bytes': size_bytes,
        'metrics': meta.get('metrics'),
    }


@multiview_3d_bp.route('/api/multiview/reconstructions')
def api_list_reconstructions():
    """List all 3D reconstructions (new engine outputs and legacy records)."""
    try:
        metadata_files = glob.glob(os.path.join(RECONSTRUCTIONS_DIR, "*.json"))

        reconstructions = []
        for meta_file in metadata_files:
            try:
                reconstructions.append(_normalize_reconstruction_meta(meta_file))
            except Exception as e:
                logger.warning(f"Skipping unreadable reconstruction metadata {meta_file}: {e}")

        reconstructions.sort(key=lambda r: r.get('timestamp', ''), reverse=True)
        return jsonify({
            'ok': True,
            'reconstructions': reconstructions
        })
        
    except Exception as e:
        logger.exception("Failed to list reconstructions")
        return jsonify({'ok': False, 'error': str(e)}), 500


@multiview_3d_bp.route('/api/multiview/download/<filename>')
def api_download_reconstruction(filename):
    """Download a PLY point cloud file"""
    from basebuddy.core.upload_safety import safe_basename

    safe = safe_basename(filename)
    if not safe:
        return jsonify({'ok': False, 'error': 'Invalid filename'}), 400
    filepath = os.path.join(RECONSTRUCTIONS_DIR, safe)

    if not os.path.exists(filepath):
        return jsonify({'ok': False, 'error': 'File not found'}), 404

    return send_file(
        filepath,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=safe
    )


@multiview_3d_bp.route('/api/multiview/quick-capture', methods=['POST'])
def api_quick_capture():
    """Capture synchronized images from all configured cameras for reconstruction"""
    try:
        import basebuddy.modules.state as shared_state
        
        data = request.json
        camera_ids = data.get('camera_ids', [])
        
        if not camera_ids:
            return jsonify({'ok': False, 'error': 'No cameras specified'}), 400
        
        # Capture from all cameras
        captured_images = {}
        
        for cam_id in camera_ids:
            if cam_id not in shared_state.grabbers:
                return jsonify({
                    'ok': False,
                    'error': f'Camera {cam_id} not available'
                }), 400
            
            grabber = shared_state.grabbers[cam_id]
            frame, _ts = grabber.get_latest_frame()
            
            if frame is None:
                return jsonify({
                    'ok': False,
                    'error': f'Could not capture from camera {cam_id}'
                }), 500
            
            # Save temporarily
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            temp_dir = os.path.join(MULTIVIEW_DIR, "temp_captures")
            Path(temp_dir).mkdir(exist_ok=True)
            
            filename = f"camera_{cam_id}_{timestamp}.jpg"
            filepath = os.path.join(temp_dir, filename)
            cv2.imwrite(filepath, frame)
            
            captured_images[cam_id] = filepath
        
        return jsonify({
            'ok': True,
            'timestamp': timestamp,
            'images': captured_images
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============ MODERN RECONSTRUCTION API (engines + jobs + plant metrics) ============

# OpenCV camera convention: +y points down in the first camera's frame, so
# "up" for a plant is -y by default. Users can override per reconstruction.
DEFAULT_UP_AXIS = '-y'


def _reconstruction_paths(recon_id: str):
    """Validated (json_path, meta) for a reconstruction id, or (None, None)."""
    from basebuddy.core.upload_safety import safe_basename
    safe = safe_basename(recon_id)
    if not safe:
        return None, None
    json_path = os.path.join(RECONSTRUCTIONS_DIR, f"{safe}.json")
    if not os.path.exists(json_path):
        return None, None
    with open(json_path, 'r') as f:
        return json_path, json.load(f)


def _run_reconstruction_job(progress_cb, engine, images, masks, camera_ids):
    """Executed on the job worker thread: reconstruct, measure, persist."""
    from basebuddy.modules.multiview.pointcloud_io import save_ply
    from basebuddy.modules.multiview.plant_metrics import compute_plant_metrics

    result = engine.reconstruct(images, masks=masks, progress_cb=progress_cb)
    if len(result.points) == 0:
        raise RuntimeError('Reconstruction produced no points')

    progress_cb(90, 'Computing plant metrics')
    metrics = compute_plant_metrics(result.points, up_axis=DEFAULT_UP_AXIS)

    progress_cb(95, 'Saving point cloud')
    now = datetime.now()
    recon_id = f"recon_{now.strftime('%Y%m%d_%H%M%S')}_{engine.id}"
    ply_file = f"{recon_id}.ply"
    ply_path = os.path.join(RECONSTRUCTIONS_DIR, ply_file)
    num_written = save_ply(ply_path, result.points, result.colors)

    meta = {
        'id': recon_id,
        'timestamp': now.isoformat(timespec='seconds'),
        'engine': engine.id,
        'camera_ids': camera_ids,
        'num_points': num_written,
        'ply_file': ply_file,
        'cameras': result.cameras,
        'metrics': metrics,
    }
    with open(os.path.join(RECONSTRUCTIONS_DIR, f"{recon_id}.json"), 'w') as f:
        json.dump(meta, f, indent=2)

    return {'reconstruction': _normalize_reconstruction_meta(
        os.path.join(RECONSTRUCTIONS_DIR, f"{recon_id}.json"))}


@multiview_3d_bp.route('/api/multiview/engines')
def api_list_engines():
    """List reconstruction engines and their availability."""
    from basebuddy.modules.multiview.engines import list_engines
    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
    except ImportError:
        cuda_available = False
    return jsonify({
        'ok': True,
        'cuda_available': cuda_available,
        'engines': list_engines(),
    })


@multiview_3d_bp.route('/api/multiview/reconstruct/start', methods=['POST'])
def api_reconstruct_start():
    """Start a reconstruction job in the background; returns a job id to poll."""
    from basebuddy.core.services.reconstruction_jobs import get_job_manager
    from basebuddy.modules.multiview.engines import resolve_engine
    from basebuddy.modules.state import grabbers

    data = request.json or {}
    try:
        camera_ids = [int(c) for c in data.get('camera_ids', [])]
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'error': 'camera_ids must be integers'}), 400
    engine_id = str(data.get('engine', 'auto'))
    use_masks = bool(data.get('use_masks', True))

    if len(camera_ids) < 2:
        return jsonify({'ok': False, 'error': 'Need at least 2 cameras'}), 400

    engine = resolve_engine(engine_id)
    if engine is None:
        return jsonify({
            'ok': False,
            'error': f"Engine '{engine_id}' is not available. "
                     "See /api/multiview/engines for options."
        }), 400

    # Capture frames in-request (fast); the heavy work happens on the worker.
    images, masks, captured_ids, failed = [], [], [], []
    for cam_id in camera_ids:
        grabber = grabbers.get(cam_id)
        frame = None
        if grabber is not None:
            frame, _ts = grabber.get_latest_frame()
        if frame is None:
            failed.append(cam_id)
            continue
        images.append(frame.copy())
        # Exclude regions (burned-in text/OSD) apply even when seg masks are off.
        masks.append(get_registration_mask(cam_id, frame.shape[:2], use_seg_mask=use_masks))
        captured_ids.append(cam_id)

    if len(images) < 2:
        return jsonify({
            'ok': False,
            'error': f'Need frames from at least 2 cameras '
                     f'(got {len(images)}; failed: {failed})'
        }), 400

    job_id = get_job_manager().submit(
        _run_reconstruction_job, engine, images,
        masks if any(m is not None for m in masks) else None, captured_ids)
    logger.info(f"Started reconstruction job {job_id} "
                f"(engine={engine.id}, cameras={captured_ids})")
    return jsonify({'ok': True, 'job_id': job_id})


@multiview_3d_bp.route('/api/multiview/jobs/<job_id>')
def api_get_job(job_id):
    """Poll a reconstruction job."""
    from basebuddy.core.services.reconstruction_jobs import get_job_manager
    job = get_job_manager().get(job_id)
    if job is None:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    return jsonify({'ok': True, 'job': job})


@multiview_3d_bp.route('/api/multiview/cloud/<filename>')
def api_get_cloud(filename):
    """Serve a PLY point cloud inline (for the in-browser viewer)."""
    from basebuddy.core.upload_safety import safe_basename
    safe = safe_basename(filename)
    if not safe or not safe.endswith('.ply'):
        return jsonify({'ok': False, 'error': 'Invalid filename'}), 400
    filepath = os.path.join(RECONSTRUCTIONS_DIR, safe)
    if not os.path.exists(filepath):
        return jsonify({'ok': False, 'error': 'File not found'}), 404
    return send_file(filepath, mimetype='application/octet-stream')


@multiview_3d_bp.route('/api/multiview/reconstruction/<recon_id>', methods=['DELETE'])
def api_delete_reconstruction(recon_id):
    """Delete a reconstruction (PLY + metadata)."""
    json_path, meta = _reconstruction_paths(recon_id)
    if json_path is None:
        return jsonify({'ok': False, 'error': 'Reconstruction not found'}), 404

    ply_file = meta.get('ply_file')
    if ply_file:
        from basebuddy.core.upload_safety import resolve_under_dir
        ply_path = resolve_under_dir(RECONSTRUCTIONS_DIR, ply_file)
        if ply_path and ply_path.exists():
            ply_path.unlink()
    os.remove(json_path)
    return jsonify({'ok': True})


@multiview_3d_bp.route('/api/multiview/reconstruction/<recon_id>/scale', methods=['POST'])
def api_set_reconstruction_scale(recon_id):
    """
    Set the real-world scale (and up axis) for a reconstruction and recompute
    plant metrics in metric units.

    Body: {known_distance_units, known_distance_m, up_axis}
       or {scale_m_per_unit, up_axis}
    """
    from basebuddy.modules.multiview.plant_metrics import UP_AXES, compute_plant_metrics
    from basebuddy.modules.multiview.pointcloud_io import load_ply

    json_path, meta = _reconstruction_paths(recon_id)
    if json_path is None:
        return jsonify({'ok': False, 'error': 'Reconstruction not found'}), 404

    data = request.json or {}
    scale = data.get('scale_m_per_unit')
    if scale is None:
        try:
            units = float(data.get('known_distance_units', 0))
            meters = float(data.get('known_distance_m', 0))
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Invalid scale values'}), 400
        if units <= 0 or meters <= 0:
            return jsonify({'ok': False,
                            'error': 'known_distance_units and known_distance_m must be > 0'}), 400
        scale = meters / units
    else:
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Invalid scale_m_per_unit'}), 400
        if scale <= 0:
            return jsonify({'ok': False, 'error': 'scale_m_per_unit must be > 0'}), 400

    up_axis = data.get('up_axis') or (meta.get('metrics') or {}).get('up_axis') or DEFAULT_UP_AXIS
    if up_axis not in UP_AXES:
        return jsonify({'ok': False, 'error': f'up_axis must be one of {UP_AXES}'}), 400

    ply_path = os.path.join(RECONSTRUCTIONS_DIR, meta.get('ply_file', ''))
    if not os.path.exists(ply_path):
        return jsonify({'ok': False, 'error': 'Point cloud file missing'}), 404

    points, _colors = load_ply(ply_path)
    metrics = compute_plant_metrics(points, up_axis=up_axis, scale_m_per_unit=scale)
    if metrics is None:
        return jsonify({'ok': False, 'error': 'Too few points to measure'}), 400

    meta['metrics'] = metrics
    with open(json_path, 'w') as f:
        json.dump(meta, f, indent=2)

    return jsonify({'ok': True, 'metrics': metrics})


@multiview_3d_bp.route('/api/multiview/growth-series')
def api_growth_series():
    """Time series of plant metrics across reconstructions (for growth charts)."""
    try:
        series = []
        for meta_file in glob.glob(os.path.join(RECONSTRUCTIONS_DIR, "*.json")):
            try:
                meta = _normalize_reconstruction_meta(meta_file)
            except Exception:
                continue
            if meta.get('metrics'):
                series.append({
                    'id': meta['id'],
                    'timestamp': meta['timestamp'],
                    'metrics': meta['metrics'],
                })
        series.sort(key=lambda s: s.get('timestamp', ''))
        return jsonify({'ok': True, 'series': series})
    except Exception as e:
        logger.exception("Failed to build growth series")
        return jsonify({'ok': False, 'error': str(e)}), 500

