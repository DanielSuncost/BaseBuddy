"""Camera listing, image listing and prompt CRUD endpoints."""

from flask import request, jsonify
import os
import glob
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import plant_tracking_bp, logger
from .helpers import STILLS_DIR, PROMPT_CONFIG_DIR, MASKS_DIR, _safe_seg
from .segmentation import analyze_prompt_pattern


@plant_tracking_bp.route('/api/plant-tracking/cameras')
def api_get_tracking_cameras():
    """Get list of cameras with timelapse stills"""
    try:
        from basebuddy.modules.camera_profiles import get_profile_manager
        profile_manager = get_profile_manager()
        
        camera_dirs = sorted(glob.glob(os.path.join(STILLS_DIR, "camera_*")))
        cameras = []
        
        for cam_dir in camera_dirs:
            camera_id = os.path.basename(cam_dir)
            images = glob.glob(os.path.join(cam_dir, "*.jpg"))
            
            if images:
                # Extract numeric camera ID for profile lookup
                try:
                    numeric_id = int(camera_id.replace('camera_', ''))
                except Exception:
                    numeric_id = None
                
                # Get camera name from profile
                name = camera_id  # Default to folder name
                if numeric_id is not None:
                    profile = profile_manager.get_profile(numeric_id)
                    if profile.name:
                        name = profile.name
                    else:
                        name = f'Camera {numeric_id + 1}'
                
                # Check if has saved prompts
                prompt_file = os.path.join(PROMPT_CONFIG_DIR, f"{camera_id}_prompts.json")
                has_prompts = os.path.exists(prompt_file)
                
                # Count processed masks
                mask_dir = os.path.join(MASKS_DIR, camera_id, "masks")
                num_masks = len(glob.glob(os.path.join(mask_dir, "*.png"))) if os.path.exists(mask_dir) else 0
                
                # Get latest image for thumbnail
                latest_image_url = None
                if images:
                    latest_image = images[-1]  # Most recent
                    latest_image_url = f'/stills/{camera_id}/{os.path.basename(latest_image)}'
                
                cameras.append({
                    'id': camera_id,
                    'name': name,
                    'num_images': len(images),
                    'has_prompts': has_prompts,
                    'num_masks': num_masks,
                    'latest_image_url': latest_image_url
                })
        
        return jsonify({'ok': True, 'cameras': cameras})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/images')
def api_get_camera_images(camera_id):
    """Get list of images for a camera"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        days_back = request.args.get('days', type=int, default=7)
        limit = request.args.get('limit', type=int, default=100)
        start_hour = request.args.get('start_hour', type=int, default=0)
        end_hour = request.args.get('end_hour', type=int, default=23)
        
        pattern = os.path.join(STILLS_DIR, camera_id, "*.jpg")
        all_images = sorted(glob.glob(pattern))
        
        # Filter by date and time
        cutoff_date = datetime.now() - timedelta(days=days_back)
        recent_images = []
        
        for img_path in all_images:
            basename = os.path.basename(img_path)
            if len(basename) >= 15:
                try:
                    # Parse YYYYMMDD_HHMMSS
                    timestamp_str = basename[:15]
                    img_datetime = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    
                    # Check date range
                    if img_datetime.date() < cutoff_date.date():
                        continue
                    
                    # Check hour range
                    img_hour = img_datetime.hour
                    if start_hour <= end_hour:
                        if not (start_hour <= img_hour <= end_hour):
                            continue
                    else:  # Handle wrap-around (e.g., 22:00 to 06:00)
                        if not (img_hour >= start_hour or img_hour <= end_hour):
                            continue
                    
                    recent_images.append(img_path)
                except Exception:
                    continue
        
        # Sample if too many - ensure we get exactly 'limit' images
        if len(recent_images) > limit:
            # Use numpy-style linspace approach for even sampling
            indices = [int(i * len(recent_images) / limit) for i in range(limit)]
            recent_images = [recent_images[i] for i in indices]
        
        # Format for frontend
        images = []
        for img_path in recent_images:
            basename = os.path.basename(img_path)
            
            # Check if mask exists
            mask_path = os.path.join(MASKS_DIR, camera_id, "masks", basename.replace('.jpg', '_mask.png'))
            has_mask = os.path.exists(mask_path)
            
            images.append({
                'filename': basename,
                'path': img_path,
                'url': f'/stills/{camera_id}/{basename}',
                'has_mask': has_mask
            })
        
        return jsonify({'ok': True, 'images': images})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/prompts')
def api_get_prompts(camera_id):
    """Get saved prompts for a camera"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        prompt_file = os.path.join(PROMPT_CONFIG_DIR, f"{camera_id}_prompts.json")
        
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r') as f:
                config = json.load(f)
            return jsonify({'ok': True, 'config': config})
        else:
            return jsonify({'ok': True, 'config': None})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/prompts', methods=['POST'])
def api_save_prompts(camera_id):
    """Save prompts for a camera"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        data = request.json
        points = data.get('points', [])
        labels = data.get('labels', [])
        image_path = data.get('image_path', '')
        
        Path(PROMPT_CONFIG_DIR).mkdir(exist_ok=True)
        
        # Analyze pattern with color profile
        pattern = analyze_prompt_pattern(points, labels, data.get('image_shape', [1080, 1920]), image_path)
        
        # Load or create config
        prompt_file = os.path.join(PROMPT_CONFIG_DIR, f"{camera_id}_prompts.json")
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r') as f:
                config = json.load(f)
        else:
            config = {'camera_id': camera_id, 'patterns': []}
        
        # Add new pattern
        config['patterns'].append({
            'timestamp': datetime.now().isoformat(),
            'reference_image': image_path,
            'pattern': pattern
        })
        
        # Save
        with open(prompt_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        return jsonify({
            'ok': True, 
            'num_patterns': len(config['patterns']),
            'has_color_profile': 'color_profile' in pattern
        })
    except Exception as e:
        import traceback
        logger.error(f"Error saving prompts: {e}")
        logger.info(traceback.format_exc())
        return jsonify({'ok': False, 'error': str(e)}), 500

@plant_tracking_bp.route('/api/plant-tracking/<camera_id>/prompts/<int:pattern_id>', methods=['DELETE'])
def api_delete_prompt_pattern(camera_id, pattern_id):
    """Delete a specific prompt pattern"""
    try:
        camera_id = _safe_seg(camera_id)
        if not camera_id:
            return jsonify({'ok': False, 'error': 'Invalid path'}), 400
        prompt_file = os.path.join(PROMPT_CONFIG_DIR, f"{camera_id}_prompts.json")
        
        if not os.path.exists(prompt_file):
            return jsonify({'ok': False, 'error': 'No prompts found'}), 404
        
        with open(prompt_file, 'r') as f:
            config = json.load(f)
        
        if 0 <= pattern_id < len(config['patterns']):
            config['patterns'].pop(pattern_id)
            
            with open(prompt_file, 'w') as f:
                json.dump(config, f, indent=2)
            
            return jsonify({'ok': True, 'remaining': len(config['patterns'])})
        else:
            return jsonify({'ok': False, 'error': 'Invalid pattern ID'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
