"""Metrics and mask-timelapse endpoints."""

from flask import request, jsonify
import os
import glob
import cv2
import numpy as np
from datetime import datetime

from . import plant_tracking_bp
from .helpers import STILLS_DIR, MASKS_DIR, _safe_seg


@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/metrics')
def api_get_metrics(camera_id):
    """Calculate metrics from masks over time"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        mask_dir = os.path.join(MASKS_DIR, camera_id, "masks")
        
        if not os.path.exists(mask_dir):
            return jsonify({'ok': True, 'metrics': []})
        
        mask_files = sorted(glob.glob(os.path.join(mask_dir, "*_mask.png")))
        
        metrics = []
        for mask_path in mask_files:
            basename = os.path.basename(mask_path).replace('_mask.png', '')
            
            # Parse timestamp
            if len(basename) >= 15:
                try:
                    timestamp = datetime.strptime(basename, '%Y%m%d_%H%M%S')
                except Exception:
                    continue
            else:
                continue
            
            # Load mask
            mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            if mask is None:
                continue
            
            # Calculate metrics
            plant_pixels = int(np.sum(mask > 0))
            total_pixels = mask.shape[0] * mask.shape[1]
            coverage = float(plant_pixels / total_pixels)
            
            # Calculate centroid
            if plant_pixels > 0:
                y_coords, x_coords = np.where(mask > 0)
                centroid_x = float(np.mean(x_coords))
                centroid_y = float(np.mean(y_coords))
            else:
                centroid_x = centroid_y = 0
            
            metrics.append({
                'timestamp': timestamp.isoformat(),
                'filename': basename + '.jpg',
                'coverage': coverage,
                'plant_pixels': plant_pixels,
                'centroid_x': centroid_x,
                'centroid_y': centroid_y
            })
        
        return jsonify({'ok': True, 'metrics': metrics})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/create-mask-timelapse', methods=['POST'])
def api_create_mask_timelapse(camera_id):
    """Create a timelapse video from segmentation masks"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        data = request.json
        frame_skip = data.get('frame_skip', 1)
        fps = data.get('fps', 15)
        format_type = data.get('format', 'mp4')
        view_mode = data.get('view_mode', 'overlay')  # overlay, mask, plant, sidebyside
        
        mask_dir = os.path.join(MASKS_DIR, camera_id, "masks")
        
        if not os.path.exists(mask_dir):
            return jsonify({'ok': False, 'error': 'No masks found'}), 404
        
        # Get all mask files sorted by timestamp
        mask_files = sorted(glob.glob(os.path.join(mask_dir, "*_mask.png")))
        
        if len(mask_files) == 0:
            return jsonify({'ok': False, 'error': 'No masks found'}), 404
        
        # Apply frame skip
        mask_files = mask_files[::frame_skip]
        
        if len(mask_files) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 masks after frame skip'}), 400
        
        # Create output directory
        output_dir = os.path.join("timelapse_output", "masks")
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{camera_id}_mask_timelapse_{timestamp}.{format_type}"
        output_path = os.path.join(output_dir, output_filename)
        
        # Create timelapse based on format
        if format_type == 'gif':
            import imageio
            frames = []
            for mask_path in mask_files:
                # Load mask
                mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
                if mask is None:
                    continue
                
                # Get corresponding original image
                basename = os.path.basename(mask_path).replace('_mask.png', '.jpg')
                img_path = os.path.join(STILLS_DIR, camera_id, basename)
                
                if view_mode == 'mask':
                    # Show mask only (convert to RGB)
                    frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
                elif view_mode == 'plant' and os.path.exists(img_path):
                    # Show plant only with black background
                    image = cv2.imread(img_path)
                    frame = image.copy()
                    frame[mask == 0] = [0, 0, 0]
                elif view_mode == 'overlay' and os.path.exists(img_path):
                    # Show overlay
                    image = cv2.imread(img_path)
                    frame = image.copy()
                    # Green overlay on mask region
                    frame[mask > 0] = (frame[mask > 0] * 0.6 + np.array([0, 255, 0]) * 0.4).astype(np.uint8)
                else:
                    # Default to mask
                    frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2RGB)
                
                # Convert BGR to RGB for imageio
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames.append(frame_rgb)
            
            # Save as GIF
            imageio.mimsave(output_path, frames, fps=fps, loop=0)
            
        else:  # mp4
            # Get first frame to determine dimensions
            first_mask = cv2.imread(mask_files[0], cv2.IMREAD_GRAYSCALE)
            h, w = first_mask.shape
            
            # Create video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
            
            for mask_path in mask_files:
                # Load mask
                mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
                if mask is None:
                    continue
                
                # Get corresponding original image
                basename = os.path.basename(mask_path).replace('_mask.png', '.jpg')
                img_path = os.path.join(STILLS_DIR, camera_id, basename)
                
                if view_mode == 'mask':
                    frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                elif view_mode == 'plant' and os.path.exists(img_path):
                    image = cv2.imread(img_path)
                    frame = image.copy()
                    frame[mask == 0] = [0, 0, 0]
                elif view_mode == 'overlay' and os.path.exists(img_path):
                    image = cv2.imread(img_path)
                    frame = image.copy()
                    frame[mask > 0] = (frame[mask > 0] * 0.6 + np.array([0, 255, 0]) * 0.4).astype(np.uint8)
                else:
                    frame = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                
                out.write(frame)
            
            out.release()
        
        # Get file size
        file_size = os.path.getsize(output_path)
        
        return jsonify({
            'ok': True,
            'filename': output_filename,
            'path': f'/timelapse_output/masks/{output_filename}',
            'frames': len(mask_files),
            'fps': fps,
            'size_mb': round(file_size / (1024 * 1024), 2)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
