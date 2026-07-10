"""
Camera Management Routes
"""
import logging

logger = logging.getLogger(__name__)

from flask import Blueprint, request, jsonify
from basebuddy.modules.config import CAM_URLS, DEF
from basebuddy.modules.camera_profiles import get_profile_manager, CameraProfile
from basebuddy.modules.state import grabbers, detectors
import os
import json

cameras_bp = Blueprint('cameras', __name__)

from basebuddy.core.paths import get_repo_root

# Camera wall state file - tracks which cameras are active on the wall
WALL_STATE_FILE = os.path.join(get_repo_root(), 'camera_wall_state.json')

def load_wall_state():
    """Load camera wall state (which cameras are active on the wall)"""
    try:
        if os.path.exists(WALL_STATE_FILE):
            with open(WALL_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.info(f"Error loading wall state: {e}")
    return {'active_cameras': [], 'version': 1}

def save_wall_state(state):
    """Save camera wall state"""
    try:
        with open(WALL_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        return True
    except Exception as e:
        logger.info(f"Error saving wall state: {e}")
        return False

def get_config_file_path():
    """Get the path to the config file"""
    # Try config.txt first, then .env
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.txt')
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    return config_path

def load_camera_config():
    """Load camera configuration from file"""
    config_path = get_config_file_path()
    cameras = []
    
    # Read from file
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key.startswith('CAM'):
                        cam_num = int(key.replace('CAM', ''))
                        cameras.append({
                            'id': cam_num - 1,
                            'name': f'Camera {cam_num}',
                            'url': value,
                            'enabled': bool(value)
                        })
    
    # Also check environment variables
    for i in range(1, 21):  # Support up to 20 cameras
        env_key = f'CAM{i}'
        url = os.environ.get(env_key, '')
        if url:
            # Check if already in cameras list
            existing = next((c for c in cameras if c['id'] == i - 1), None)
            if not existing:
                cameras.append({
                    'id': i - 1,
                    'name': f'Camera {i}',
                    'url': url,
                    'enabled': True
                })
    
    # Update camera names from profiles
    try:
        profile_manager = get_profile_manager()
        for camera in cameras:
            profile = profile_manager.get_profile(camera['id'])
            if profile and profile.name:
                camera['name'] = profile.name
    except Exception as e:
        logger.info(f"Warning: Could not load camera profiles: {e}")
    
    return sorted(cameras, key=lambda x: x['id'])

def save_camera_config(cameras):
    """Save camera configuration to file"""
    config_path = get_config_file_path()
    
    # Read existing config
    lines = []
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            lines = f.readlines()
    
    # Update CAM entries
    updated_lines = []
    cam_keys_updated = set()
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            updated_lines.append(line)
            continue
        
        if '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key.startswith('CAM'):
                cam_num = int(key.replace('CAM', ''))
                cam_id = cam_num - 1
                cam = next((c for c in cameras if c['id'] == cam_id), None)
                if cam:
                    updated_lines.append(f'{key}="{cam["url"]}"\n')
                    cam_keys_updated.add(cam_id)
                    continue
        
        updated_lines.append(line)
    
    # Add any new cameras
    for cam in cameras:
        if cam['id'] not in cam_keys_updated:
            cam_num = cam['id'] + 1
            updated_lines.append(f'CAM{cam_num}="{cam["url"]}"\n')
    
    # Write back
    with open(config_path, 'w') as f:
        f.writelines(updated_lines)
    
    # Also update environment variables for current session
    for cam in cameras:
        cam_num = cam['id'] + 1
        os.environ[f'CAM{cam_num}'] = cam['url']

@cameras_bp.route('/api/cameras')
def api_get_cameras():
    """Get list of all cameras"""
    cameras = load_camera_config()
    return jsonify({'cameras': cameras})

@cameras_bp.route('/api/cameras', methods=['POST'])
def api_add_camera():
    """Add or update a camera"""
    try:
        logger.info("[API] POST /api/cameras called")
        data = request.json
        logger.info(f"[API] Request data: {data}")
        
        url = data.get('url', '').strip()
        name = data.get('name', '').strip()
        cam_id = data.get('id')  # Optional: specific camera ID
        
        if not url:
            logger.info("[API] Error: URL is required")
            return jsonify({'ok': False, 'error': 'URL is required'}), 400
        
        logger.info("[API] Loading camera config...")
        cameras = load_camera_config()
        logger.info(f"[API] Loaded {len(cameras)} cameras")
        added_cam_id = None
        
        if cam_id is not None:
            # Update existing camera
            cam = next((c for c in cameras if c['id'] == cam_id), None)
            if cam:
                cam['url'] = url
                if name:
                    cam['name'] = name
                added_cam_id = cam_id
                logger.info(f"[API] Updated camera {cam_id}")
            else:
                return jsonify({'ok': False, 'error': f'Camera {cam_id} not found'}), 404
        else:
            # Find first empty slot or add new
            max_id = max([c['id'] for c in cameras], default=-1)
            new_id = max_id + 1
            
            # Check if we're replacing an empty slot
            empty_cam = next((c for c in cameras if not c['url']), None)
            if empty_cam:
                empty_cam['url'] = url
                if name:
                    empty_cam['name'] = name
                added_cam_id = empty_cam['id']
                logger.info(f"[API] Reusing empty slot {added_cam_id}")
            else:
                cameras.append({
                    'id': new_id,
                    'name': name or f'Camera {new_id + 1}',
                    'url': url,
                    'enabled': True
                })
                added_cam_id = new_id
                logger.info(f"[API] Created new camera {added_cam_id}")
        
        logger.info("[API] Saving camera config...")
        save_camera_config(cameras)
        logger.info("[API] Camera config saved")
        
        # Also add to wall state (make it active on the wall)
        if added_cam_id is not None:
            logger.info("[API] Loading wall state...")
            state = load_wall_state()
            if added_cam_id not in state.get('active_cameras', []):
                if 'active_cameras' not in state:
                    state['active_cameras'] = []
                state['active_cameras'].append(added_cam_id)
                state['active_cameras'].sort()
                logger.info(f"[API] Saving wall state with cameras: {state['active_cameras']}")
                save_wall_state(state)
            
            # Hot-add the camera without restarting (uses modular system_init)
            try:
                from basebuddy.modules.system_init import add_single_camera
                cam_data = next((c for c in cameras if c['id'] == added_cam_id), None)
                if cam_data and cam_data.get('url'):
                    logger.info(f"[API] Hot-adding camera {added_cam_id}...")
                    success = add_single_camera(added_cam_id, cam_data['url'])
                    if success:
                        logger.info(f"[API] Camera {added_cam_id} successfully hot-added!")
                    else:
                        logger.info(f"[API] Warning: Failed to hot-add camera {added_cam_id}")
            except Exception as e:
                logger.info(f"[API] Error hot-adding camera: {e}")
                import traceback
                traceback.print_exc()
        
        logger.info("[API] Sending success response")
        return jsonify({
            'ok': True,
            'message': 'Camera added and started successfully!',
            'camera_id': added_cam_id
        })
    except Exception as e:
        logger.exception("api_add_camera failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500

@cameras_bp.route('/api/cameras/<int:cam_id>', methods=['DELETE'])
def api_delete_camera(cam_id):
    """Remove a camera (clear its URL) - permanently deletes the camera config"""
    try:
        cameras = load_camera_config()
        cam = next((c for c in cameras if c['id'] == cam_id), None)
        
        if not cam:
            return jsonify({'ok': False, 'error': f'Camera {cam_id} not found'}), 404
        
        cam['url'] = ''
        cam['enabled'] = False
        
        save_camera_config(cameras)
        
        # Also remove from wall state
        state = load_wall_state()
        if cam_id in state.get('active_cameras', []):
            state['active_cameras'].remove(cam_id)
            save_wall_state(state)
        
        # Stop the grabber so the deleted camera stops streaming immediately
        try:
            if cam_id in grabbers:
                grabbers[cam_id].stop()
                logger.info(f"[API] Stopped grabber for deleted camera {cam_id + 1}")
        except Exception as stop_err:
            logger.warning(f"[API] Could not stop grabber for camera {cam_id + 1}: {stop_err}")
        
        return jsonify({
            'ok': True,
            'message': f'Camera {cam_id + 1} deleted permanently'
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ============ Camera Wall State APIs ============

@cameras_bp.route('/api/wall/cameras')
def api_get_wall_cameras():
    """Get cameras currently active on the wall"""
    logger.info("[API] GET /api/wall/cameras called")
    try:
        state = load_wall_state()
        all_cameras = load_camera_config()
        active_ids = state.get('active_cameras', [])
        logger.info(f"[API] State active_ids: {active_ids}, All cameras: {len(all_cameras)}")
        
        # If no state exists yet, default to showing all configured cameras
        if not active_ids and not os.path.exists(WALL_STATE_FILE):
            active_ids = [c['id'] for c in all_cameras if c.get('url')]
            state['active_cameras'] = active_ids
            save_wall_state(state)
            logger.info(f"[API] Initialized state with: {active_ids}")
        
        # Filter to only active cameras with URLs
        active_cameras = [c for c in all_cameras if c['id'] in active_ids and c.get('url')]
        inactive_cameras = [c for c in all_cameras if c['id'] not in active_ids and c.get('url')]
        
        logger.info(f"[API] Returning {len(active_cameras)} active, {len(inactive_cameras)} inactive")
        return jsonify({
            'ok': True,
            'active': active_cameras,
            'inactive': inactive_cameras,
            'all': all_cameras
        })
    except Exception as e:
        logger.exception("[API] Error handling camera request")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500

@cameras_bp.route('/api/wall/cameras/<int:cam_id>/activate', methods=['POST'])
def api_activate_camera(cam_id):
    """Add a camera to the wall (activate it)"""
    try:
        state = load_wall_state()
        if cam_id not in state.get('active_cameras', []):
            if 'active_cameras' not in state:
                state['active_cameras'] = []
            state['active_cameras'].append(cam_id)
            state['active_cameras'].sort()
            save_wall_state(state)
        
        return jsonify({
            'ok': True,
            'message': f'Camera {cam_id + 1} added to wall'
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@cameras_bp.route('/api/wall/cameras/<int:cam_id>/deactivate', methods=['POST'])
def api_deactivate_camera(cam_id):
    """Remove a camera from the wall (deactivate it, but keep config)"""
    logger.info(f"[API] POST /api/wall/cameras/{cam_id}/deactivate called")
    try:
        state = load_wall_state()
        logger.info(f"[API] Current active cameras: {state.get('active_cameras', [])}")
        if cam_id in state.get('active_cameras', []):
            state['active_cameras'].remove(cam_id)
            save_wall_state(state)
            logger.info(f"[API] Camera {cam_id} removed. New active: {state['active_cameras']}")
        else:
            logger.info(f"[API] Camera {cam_id} was not in active list")
        
        # IMPORTANT: Stop the grabber to prevent connection spam
        try:
            if cam_id in grabbers:
                grabber = grabbers[cam_id]
                grabber.stop()
                logger.info(f"[API]  Stopped grabber for camera {cam_id + 1}")
        except Exception as stop_err:
            logger.info(f"[API] Warning: Could not stop grabber: {stop_err}")
        
        logger.info("[API] Sending success response")
        return jsonify({
            'ok': True,
            'message': f'Camera {cam_id + 1} removed from wall (configuration preserved)'
        })
    except Exception as e:
        logger.info(f"[API] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)}), 500

@cameras_bp.route('/api/cameras/reload', methods=['POST'])
def api_reload_cameras():
    """Reload cameras dynamically without restart - adds new cameras only (uses modular state)."""
    logger.info("[RELOAD] /api/cameras/reload called")
    try:
        from basebuddy.modules.system_init import add_single_camera
        from basebuddy.modules.state import grabbers

        # Reload config file to update environment variables
        logger.info("[RELOAD] Loading config file...")
        from basebuddy.modules.config import load_config_file
        load_config_file()

        # Get current cameras from config
        cameras = load_camera_config()
        added = 0
        restarted = 0

        for cam in cameras:
            cam_id = cam['id']
            url = cam.get('url', '')

            # Skip if no URL
            if not url:
                continue

            # Check if camera exists with different URL
            if cam_id in grabbers:
                existing_grabber = grabbers[cam_id]
                if getattr(existing_grabber, 'url', '') != url:
                    logger.info(f"[RELOAD] Camera {cam_id + 1} URL changed, restarting...")
                    existing_grabber.stop()
                    if add_single_camera(cam_id, url):
                        restarted += 1
                    continue
                elif getattr(existing_grabber, 'running', False):
                    # Already running with correct URL, skip
                    continue

            # Add new camera
            logger.info(f"[RELOAD] Adding camera {cam_id + 1}...")
            if add_single_camera(cam_id, url):
                added += 1

        total_changes = added + restarted
        msg_parts = []
        if added > 0:
            msg_parts.append(f'Added {added} new camera(s)')
        if restarted > 0:
            msg_parts.append(f'Restarted {restarted} modified camera(s)')
        message = '. '.join(msg_parts) if msg_parts else 'All cameras already running'

        logger.info(f"[RELOAD] Success! {message}. Total: {len(grabbers)} active")
        return jsonify({
            'ok': True,
            'message': f'{message}. {len(grabbers)} cameras active.',
            'cameras_count': len(grabbers),
            'added': added,
            'restarted': restarted
        })
    except Exception as e:
        logger.exception("Failed to reload cameras")
        return jsonify({
            'ok': False,
            'error': str(e),
            'message': 'Failed to reload cameras. Please restart the application.'
        }), 500

@cameras_bp.route('/api/cameras/<int:camera_id>/profile')
def api_get_camera_profile(camera_id):
    """Get camera profile"""
    try:
        manager = get_profile_manager()
        profile = manager.get_profile(camera_id)
        return jsonify({
            'ok': True,
            'profile': manager.to_dict(profile)
        })
    except Exception as e:
        import traceback
        logger.info(f"[API] Error getting profile for camera {camera_id}: {e}")
        traceback.print_exc()
        # Return a default profile on error instead of 500
        return jsonify({
            'ok': True,
            'profile': {
                'camera_id': camera_id,
                'camera_enabled': True,
                'detection_enabled': True,
                'name': f'Camera {camera_id + 1}'
            }
        })

@cameras_bp.route('/api/profile-templates')
def api_get_profile_templates():
    """Get available profile templates"""
    from basebuddy.modules.camera_profiles import PROFILE_TEMPLATES
    return jsonify({
        'ok': True,
        'templates': PROFILE_TEMPLATES
    })


@cameras_bp.route('/api/cameras/<int:camera_id>/apply-template', methods=['POST'])
def api_apply_template(camera_id):
    """Apply a profile template to a camera"""
    from basebuddy.modules.camera_profiles import apply_template
    try:
        data = request.json
        template_name = data.get('template')
        if not template_name:
            return jsonify({'ok': False, 'error': 'Template name required'}), 400
        
        manager = get_profile_manager()
        profile = manager.get_profile(camera_id)
        profile = apply_template(profile, template_name)
        manager.save_profile(profile)
        
        return jsonify({
            'ok': True,
            'message': f'Template "{template_name}" applied',
            'profile': manager.to_dict(profile)
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@cameras_bp.route('/api/cameras/<int:camera_id>/profile', methods=['POST'])
def api_save_camera_profile(camera_id):
    """Save camera profile"""
    try:
        data = request.json
        logger.info(f"api_save_camera_profile({camera_id}) payload: {data}")
        manager = get_profile_manager()
        
        # Get existing profile or create new
        profile = manager.get_profile(camera_id)
        
        # Update with provided data - basic fields
        if 'camera_enabled' in data:
            profile.camera_enabled = bool(data['camera_enabled'])
        if 'detection_enabled' in data:
            profile.detection_enabled = bool(data['detection_enabled'])
        if 'name' in data:
            profile.name = data['name']
        if 'purpose' in data:
            profile.purpose = data['purpose']
        if 'template_name' in data:
            profile.template_name = data['template_name']
        
        # Timelapse/still capture
        if 'still_capture_enabled' in data:
            profile.still_capture_enabled = bool(data['still_capture_enabled'])
        if 'still_capture_interval_seconds' in data:
            profile.still_capture_interval_seconds = int(data['still_capture_interval_seconds'])
        if 'still_capture_folder' in data:
            profile.still_capture_folder = data['still_capture_folder']
        if 'still_capture_start_hour' in data:
            profile.still_capture_start_hour = int(data['still_capture_start_hour'])
        if 'still_capture_end_hour' in data:
            profile.still_capture_end_hour = int(data['still_capture_end_hour'])
        if 'still_capture_skip_dark' in data:
            profile.still_capture_skip_dark = bool(data['still_capture_skip_dark'])
        if 'still_capture_min_brightness' in data:
            profile.still_capture_min_brightness = int(data['still_capture_min_brightness'])
        
        # Rotation/flip
        if 'rotation' in data:
            profile.rotation = int(data['rotation']) % 360
        if 'flip_horizontal' in data:
            profile.flip_horizontal = bool(data['flip_horizontal'])
        if 'flip_vertical' in data:
            profile.flip_vertical = bool(data['flip_vertical'])
        
        # Save profile
        manager.save_profile(profile)
        
        # Apply profile to running grabber and detector
        try:
            if camera_id in grabbers:
                grabber = grabbers[camera_id]
                old_enabled = getattr(grabber, 'camera_enabled', True)
                grabber.camera_enabled = profile.camera_enabled
                logger.info(f"Camera {camera_id+1}: Updated camera_enabled from {old_enabled} to {profile.camera_enabled}")

                if profile.camera_enabled:
                    # Ensure grabbing thread is running (it may have been stopped manually)
                    if not grabber.running or not (grabber.thread and grabber.thread.is_alive()):
                        logger.info(f"Restarting camera {camera_id+1} grabber")
                        grabber.start()
                else:
                    # Pause camera feed without blocking the main thread
                    if grabber.cap:
                        try:
                            grabber.cap.release()
                        except Exception:
                            pass
                        grabber.cap = None
                    grabber.clear_cached_frames()
                    logger.info(f"Camera {camera_id+1}: feed disabled and buffers cleared")

            if camera_id in detectors:
                detector = detectors[camera_id]
                detector.detection_enabled = profile.detection_enabled and profile.camera_enabled
                logger.info(
                    f"Camera {camera_id+1}: Camera {'ON' if profile.camera_enabled else 'OFF'}, "
                    f"Detection {'ON' if profile.detection_enabled else 'OFF'}"
                )
        except Exception as apply_err:
            import traceback
            logger.info(f"Warning: Could not apply profile: {apply_err}")
            logger.info(traceback.format_exc())
        
        return jsonify({
            'ok': True,
            'message': 'Profile saved successfully',
            'profile': manager.to_dict(profile)
        })
    except Exception as e:
        logger.exception("Camera API request failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500


# ============ Camera Groups API ============

@cameras_bp.route('/api/camera-groups')
def api_get_camera_groups():
    """Get all camera groups"""
    from basebuddy.modules.camera_groups import load_groups, to_dict, AVAILABLE_ICONS
    groups = load_groups()
    return jsonify({
        'ok': True,
        'groups': [to_dict(g) for g in groups],
        'available_icons': AVAILABLE_ICONS
    })


@cameras_bp.route('/api/camera-groups', methods=['POST'])
def api_create_camera_group():
    """Create a new camera group"""
    from basebuddy.modules.camera_groups import create_group, to_dict
    try:
        data = request.json
        name = data.get('name', 'New Group')
        icon = data.get('icon', 'folder')
        camera_ids = data.get('camera_ids', [])
        color = data.get('color', '#1a73e8')
        
        group = create_group(name=name, icon=icon, camera_ids=camera_ids, color=color)
        return jsonify({
            'ok': True,
            'group': to_dict(group)
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@cameras_bp.route('/api/camera-groups/<group_id>', methods=['PUT'])
def api_update_camera_group(group_id):
    """Update a camera group"""
    from basebuddy.modules.camera_groups import update_group, to_dict
    try:
        data = request.json
        group = update_group(group_id, **data)
        if group:
            return jsonify({
                'ok': True,
                'group': to_dict(group)
            })
        return jsonify({'ok': False, 'error': 'Group not found'}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@cameras_bp.route('/api/camera-groups/<group_id>', methods=['DELETE'])
def api_delete_camera_group(group_id):
    """Delete a camera group"""
    from basebuddy.modules.camera_groups import delete_group
    try:
        if delete_group(group_id):
            return jsonify({'ok': True})
        return jsonify({'ok': False, 'error': 'Failed to delete group'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@cameras_bp.route('/api/camera-groups/<group_id>/cameras/<int:camera_id>', methods=['POST'])
def api_add_camera_to_group(group_id, camera_id):
    """Add a camera to a group"""
    from basebuddy.modules.camera_groups import add_camera_to_group
    if add_camera_to_group(group_id, camera_id):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Failed to add camera'}), 500


@cameras_bp.route('/api/camera-groups/<group_id>/cameras/<int:camera_id>', methods=['DELETE'])
def api_remove_camera_from_group(group_id, camera_id):
    """Remove a camera from a group"""
    from basebuddy.modules.camera_groups import remove_camera_from_group
    if remove_camera_from_group(group_id, camera_id):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Failed to remove camera'}), 500
