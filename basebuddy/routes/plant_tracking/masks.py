"""Segmentation execution and mask CRUD/serving endpoints."""

from flask import request, jsonify, send_file
import os
import glob
import json
import io
import cv2
import numpy as np
from datetime import datetime
from pathlib import Path

from . import plant_tracking_bp, logger
from .helpers import STILLS_DIR, PROMPT_CONFIG_DIR, MASKS_DIR, _safe_seg, _safe_still_path, get_sam_predictor
from .segmentation import apply_pattern, segment_with_prompts


@plant_tracking_bp.route('/api/plant-tracking/segment', methods=['POST'])
def api_segment_images():
    """Segment selected images with SAM"""
    try:
        data = request.json
        camera_id = _safe_seg(data.get('camera_id'))
        image_filenames = [f for f in (_safe_seg(x) for x in data.get('images', [])) if f]
        pattern_id = data.get('pattern_id', -1)  # -1 = most recent

        if not camera_id or not image_filenames:
            return jsonify({'ok': False, 'error': 'Missing or invalid parameters'}), 400
        
        # Load prompts
        prompt_file = os.path.join(PROMPT_CONFIG_DIR, f"{camera_id}_prompts.json")
        logger.info(f"Looking for prompts at: {prompt_file}")
        
        if not os.path.exists(prompt_file):
            logger.error(f"Prompt file not found!")
            return jsonify({'ok': False, 'error': 'No prompts saved for this camera. Please click an image, add points, and click "Save Pattern" first.'}), 400
        
        with open(prompt_file, 'r') as f:
            config = json.load(f)
        
        if not config['patterns']:
            logger.error(f"No patterns in config!")
            return jsonify({'ok': False, 'error': 'No patterns found'}), 400
        
        pattern = config['patterns'][pattern_id]['pattern']
        logger.info(f"Using pattern {pattern_id}: {len(pattern.get('fg_points_relative', []))} fg, {len(pattern.get('bg_points_relative', []))} bg points")
        
        # Load SAM
        predictor = get_sam_predictor()
        if predictor is None:
            return jsonify({'ok': False, 'error': 'SAM not available'}), 500
        
        # Get color profile and hybrid mode from pattern
        color_profile = pattern.get('color_profile', None)
        hybrid_mode = data.get('hybrid_mode', 'union')  # union, intersection, sam_only, color_only
        
        logger.info(f"Hybrid mode: {hybrid_mode}, Has color profile: {color_profile is not None}")
        
        # Process images
        results = []
        Path(os.path.join(MASKS_DIR, camera_id, "masks")).mkdir(parents=True, exist_ok=True)
        
        for filename in image_filenames:
            img_path = os.path.join(STILLS_DIR, camera_id, filename)
            
            if not os.path.exists(img_path):
                logger.warning(f"Image not found: {img_path}")
                continue
            
            # Load image
            image = cv2.imread(img_path)
            if image is None:
                logger.error(f"Failed to load: {img_path}")
                continue
            
            # Generate prompts from pattern
            points, labels = apply_pattern(pattern, image.shape)
            
            # Segment with hybrid method
            mask = segment_with_prompts(predictor, image, points, labels, color_profile, hybrid_mode)
            
            # Save mask
            mask_filename = filename.replace('.jpg', '_mask.png')
            mask_path = os.path.join(MASKS_DIR, camera_id, "masks", mask_filename)
            cv2.imwrite(mask_path, mask)
            
            # Calculate metrics
            plant_pixels = int(np.sum(mask > 0))
            total_pixels = mask.shape[0] * mask.shape[1]
            coverage = float(plant_pixels / total_pixels)
            
            method_str = "SAM+Color" if color_profile and hybrid_mode == 'union' else hybrid_mode.upper()
            logger.info(f"Segmented {filename}: {coverage*100:.1f}% coverage ({method_str})")
            
            results.append({
                'filename': filename,
                'coverage': coverage,
                'plant_pixels': plant_pixels,
                'method': method_str
            })
        
        logger.info(f"Segmentation complete: {len(results)} images processed")
        return jsonify({'ok': True, 'results': results})
    except Exception as e:
        logger.exception("Segmentation error")
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/preview-mask', methods=['POST'])
def api_preview_mask(camera_id):
    """Generate live preview mask for annotation"""
    try:
        data = request.json
        image_path = _safe_still_path(data.get('image_path', ''))
        points = data.get('points', [])
        labels = data.get('labels', [])
        
        if not image_path:
            return jsonify({'ok': False, 'error': 'Image not found'}), 404
        
        # Load image
        image = cv2.imread(image_path)
        if image is None:
            return jsonify({'ok': False, 'error': 'Could not load image'}), 500
        
        # Get SAM predictor
        predictor = get_sam_predictor()
        if predictor is None:
            return jsonify({'ok': False, 'error': 'SAM not available'}), 500
        
        # Generate mask
        predictor.set_image(image)
        point_coords = np.array(points)
        point_labels = np.array(labels)
        
        masks, _, _ = predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            multimask_output=False
        )
        
        mask = (masks[0] * 255).astype(np.uint8)
        
        # Convert to colored overlay (green for plant)
        overlay = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
        overlay[mask > 0] = [0, 255, 0, 180]  # Green with alpha
        
        # Encode as PNG
        import io
        _, buffer = cv2.imencode('.png', overlay)
        
        return send_file(
            io.BytesIO(buffer),
            mimetype='image/png',
            as_attachment=False
        )
    except Exception as e:
        logger.error(f"Preview error: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/mask/<filename>')
def api_get_mask(camera_id, filename):
    """Get mask for a specific image"""
    try:
        camera_id, filename = _safe_seg(camera_id), _safe_seg(filename)
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        mask_filename = filename.replace('.jpg', '_mask.png')
        mask_path = os.path.join(MASKS_DIR, camera_id, "masks", mask_filename)
        
        if os.path.exists(mask_path):
            return send_file(mask_path, mimetype='image/png')
        else:
            return jsonify({'ok': False, 'error': 'Mask not found'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/plant_only/<filename>')
def api_get_plant_only(camera_id, filename):
    """Get plant extracted with background removed (black or custom color)"""
    try:
        camera_id, filename = _safe_seg(camera_id), _safe_seg(filename)
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        # Get original image and mask
        image_path = os.path.join(STILLS_DIR, camera_id, filename)
        mask_filename = filename.replace('.jpg', '_mask.png')
        mask_path = os.path.join(MASKS_DIR, camera_id, "masks", mask_filename)
        
        if not os.path.exists(image_path) or not os.path.exists(mask_path):
            return jsonify({'ok': False, 'error': 'Image or mask not found'}), 404
        
        # Load image and mask
        image = cv2.imread(image_path)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        
        if image is None or mask is None:
            return jsonify({'ok': False, 'error': 'Could not load image or mask'}), 500
        
        # Get background color from query params (hex color, default black)
        bg_color_hex = request.args.get('bg_color', '000000')
        # Convert hex to BGR
        bg_color = tuple(int(bg_color_hex[i:i+2], 16) for i in (4, 2, 0))  # RGB to BGR
        
        # Create output with custom background
        output = np.full_like(image, bg_color, dtype=np.uint8)
        
        # Copy plant pixels from original image where mask is white
        mask_bool = mask > 127
        output[mask_bool] = image[mask_bool]
        
        # Encode as JPEG
        _, buffer = cv2.imencode('.jpg', output, [cv2.IMWRITE_JPEG_QUALITY, 90])
        
        return send_file(
            io.BytesIO(buffer.tobytes()),
            mimetype='image/jpeg',
            as_attachment=False
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/overlay/<filename>')
def api_get_overlay(camera_id, filename):
    """Get original image with mask overlay"""
    try:
        camera_id, filename = _safe_seg(camera_id), _safe_seg(filename)
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        # Get original image and mask
        image_path = os.path.join(STILLS_DIR, camera_id, filename)
        mask_filename = filename.replace('.jpg', '_mask.png')
        mask_path = os.path.join(MASKS_DIR, camera_id, "masks", mask_filename)
        
        if not os.path.exists(image_path) or not os.path.exists(mask_path):
            return jsonify({'ok': False, 'error': 'Image or mask not found'}), 404
        
        # Load image and mask
        image = cv2.imread(image_path)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        
        if image is None or mask is None:
            return jsonify({'ok': False, 'error': 'Could not load image or mask'}), 500
        
        # Get opacity from query params (0-100, default 50)
        opacity = float(request.args.get('opacity', 50)) / 100.0
        
        # Get overlay color from query params (hex color, default green)
        color_hex = request.args.get('color', '10b981')  # Default green
        # Convert hex to BGR
        overlay_color = tuple(int(color_hex[i:i+2], 16) for i in (4, 2, 0))  # RGB to BGR
        
        # Create colored overlay where mask is white
        overlay = image.copy()
        mask_bool = mask > 127
        overlay[mask_bool] = overlay_color
        
        # Blend original with overlay
        output = cv2.addWeighted(image, 1 - opacity, overlay, opacity, 0)
        
        # Encode as JPEG
        _, buffer = cv2.imencode('.jpg', output, [cv2.IMWRITE_JPEG_QUALITY, 90])
        
        return send_file(
            io.BytesIO(buffer.tobytes()),
            mimetype='image/jpeg',
            as_attachment=False
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/mask/<filename>', methods=['DELETE'])
def api_delete_mask(camera_id, filename):
    """Delete/discard a specific mask"""
    try:
        camera_id, filename = _safe_seg(camera_id), _safe_seg(filename)
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        mask_filename = filename.replace('.jpg', '_mask.png')
        mask_path = os.path.join(MASKS_DIR, camera_id, "masks", mask_filename)
        
        if os.path.exists(mask_path):
            os.remove(mask_path)
            return jsonify({'ok': True, 'message': 'Mask deleted'})
        else:
            return jsonify({'ok': False, 'error': 'Mask not found'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/masks', methods=['DELETE'])
def api_clear_masks(camera_id):
    """Clear all masks for a camera"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        mask_dir = os.path.join(MASKS_DIR, camera_id, "masks")
        
        if os.path.exists(mask_dir):
            import shutil
            shutil.rmtree(mask_dir)
            os.makedirs(mask_dir, exist_ok=True)
            return jsonify({'ok': True, 'message': 'All masks cleared'})
        else:
            return jsonify({'ok': True, 'message': 'No masks to clear'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/regenerate-mask', methods=['POST'])
def api_regenerate_mask(camera_id):
    """Regenerate a mask for a specific image using currently active patterns"""
    try:
        camera_id = _safe_seg(camera_id)
        data = request.json
        filename = _safe_seg(data.get('filename'))
        
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Missing or invalid parameters'}), 400
        
        # Get the mask directory
        from basebuddy.modules.config import PLANT_TRACKING_ROOT
        mask_dir = os.path.join(PLANT_TRACKING_ROOT, camera_id, 'masks')
        
        if not os.path.exists(mask_dir):
            return jsonify({'ok': False, 'error': 'Mask directory not found'}), 404
        
        # Get the original image path
        from basebuddy.modules.config import STILLS_ROOT
        image_path = os.path.join(STILLS_ROOT, camera_id, filename)
        
        if not os.path.exists(image_path):
            return jsonify({'ok': False, 'error': 'Original image not found'}), 404
        
        # Load active patterns for this camera
        patterns = load_patterns(camera_id)
        active_patterns = [p for p in patterns if p.get('active', True)]
        
        if not active_patterns:
            return jsonify({'ok': False, 'error': 'No active patterns found'}), 400
        
        # Extract colors from all active patterns
        all_colors = []
        for pattern in active_patterns:
            if 'colors' in pattern:
                all_colors.extend(pattern['colors'])
        
        if not all_colors:
            return jsonify({'ok': False, 'error': 'No colors in active patterns'}), 400
        
        # Load the image and generate mask
        import cv2
        import numpy as np
        
        image = cv2.imread(image_path)
        if image is None:
            return jsonify({'ok': False, 'error': 'Failed to read image'}), 500
        
        # Convert to RGB for color matching
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # Create mask based on color similarity
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        
        for color in all_colors:
            # Convert hex to RGB
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            target_color = np.array([r, g, b])
            
            # Calculate color distance (using simple Euclidean distance)
            color_diff = np.linalg.norm(image_rgb - target_color, axis=2)
            
            # Threshold (adjust sensitivity here)
            threshold = 50  # Lower = more strict
            color_mask = (color_diff < threshold).astype(np.uint8) * 255
            
            # Combine with existing mask
            mask = cv2.bitwise_or(mask, color_mask)
        
        # Apply morphological operations to clean up the mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        
        # Save the mask
        mask_path = os.path.join(mask_dir, filename)
        cv2.imwrite(mask_path, mask)
        
        # Update the metadata to mark this image as having a mask
        from basebuddy.modules.config import PLANT_TRACKING_ROOT
        metadata_path = os.path.join(PLANT_TRACKING_ROOT, camera_id, 'metadata.json')
        
        metadata = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
        
        if 'masks' not in metadata:
            metadata['masks'] = {}
        
        metadata['masks'][filename] = {
            'created': datetime.now().isoformat(),
            'has_mask': True
        }
        
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        return jsonify({
            'ok': True,
            'message': 'Mask regenerated successfully'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/remove-mask', methods=['POST'])
def api_remove_mask(camera_id):
    """Remove a mask for a specific image"""
    try:
        camera_id = _safe_seg(camera_id)
        data = request.json
        filename = _safe_seg(data.get('filename'))
        
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Missing or invalid parameters'}), 400
        
        # Get the mask directory
        from basebuddy.modules.config import PLANT_TRACKING_ROOT
        mask_dir = os.path.join(PLANT_TRACKING_ROOT, camera_id, 'masks')
        mask_path = os.path.join(mask_dir, filename)
        
        # Delete the mask file if it exists
        if os.path.exists(mask_path):
            os.remove(mask_path)
        
        # Update the metadata
        metadata_path = os.path.join(PLANT_TRACKING_ROOT, camera_id, 'metadata.json')
        
        metadata = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
        
        if 'masks' in metadata and filename in metadata['masks']:
            del metadata['masks'][filename]
            
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        
        return jsonify({
            'ok': True,
            'message': 'Mask removed successfully'
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/mask-to-pattern', methods=['POST'])
def api_mask_to_pattern(camera_id):
    """Convert an existing mask to a pattern by extracting colors from the masked region"""
    try:
        camera_id = _safe_seg(camera_id)
        data = request.json
        filename = _safe_seg(data.get('filename'))
        pattern_name = data.get('pattern_name', f'Pattern from {filename}')
        
        if not camera_id or not filename:
            return jsonify({'ok': False, 'error': 'Missing or invalid parameters'}), 400
        
        # Get the image and mask paths
        from basebuddy.modules.config import STILLS_ROOT, PLANT_TRACKING_ROOT
        image_path = os.path.join(STILLS_ROOT, camera_id, filename)
        mask_path = os.path.join(PLANT_TRACKING_ROOT, camera_id, 'masks', filename)
        
        if not os.path.exists(image_path):
            return jsonify({'ok': False, 'error': 'Original image not found'}), 404
        
        if not os.path.exists(mask_path):
            return jsonify({'ok': False, 'error': 'Mask not found'}), 404
        
        # Load the image and mask
        import cv2
        import numpy as np
        
        image = cv2.imread(image_path)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        
        if image is None or mask is None:
            return jsonify({'ok': False, 'error': 'Failed to read image or mask'}), 500
        
        # Extract colors from the masked region
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        
        # Get pixels where mask is active
        masked_pixels = image_rgb[mask > 127]
        
        if len(masked_pixels) == 0:
            return jsonify({'ok': False, 'error': 'No pixels in mask'}), 400
        
        # Use k-means to find dominant colors
        from sklearn.cluster import KMeans
        
        # Limit number of pixels for performance
        if len(masked_pixels) > 10000:
            indices = np.random.choice(len(masked_pixels), 10000, replace=False)
            masked_pixels = masked_pixels[indices]
        
        # Find 5-10 dominant colors
        n_colors = min(8, len(masked_pixels) // 100)
        n_colors = max(5, n_colors)
        
        kmeans = KMeans(n_clusters=n_colors, random_state=42, n_init=10)
        kmeans.fit(masked_pixels)
        
        # Convert cluster centers to hex colors
        colors = []
        for center in kmeans.cluster_centers_:
            r, g, b = int(center[0]), int(center[1]), int(center[2])
            hex_color = f'#{r:02x}{g:02x}{b:02x}'
            colors.append(hex_color)
        
        # Save as a new pattern
        pattern = {
            'name': pattern_name,
            'colors': colors,
            'active': True,
            'created': datetime.now().isoformat(),
            'source': 'mask',
            'source_filename': filename
        }
        
        # Load existing patterns
        patterns = load_patterns(camera_id)
        patterns.append(pattern)
        
        # Save patterns
        if not save_patterns(camera_id, patterns):
            return jsonify({'ok': False, 'error': 'Failed to save pattern'}), 500
        
        return jsonify({
            'ok': True,
            'message': 'Pattern created from mask',
            'colors_count': len(colors),
            'pattern': pattern
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500
