"""
Tracking configuration API endpoints.

Manages object tracking configuration at global and per-camera levels.
Settings are persisted as the TRACKING_CONFIG export in config.txt
({"global": {...}, "cameras": {"<id>": {...}}}) and applied to live
detectors on save. The detector applies the same overrides at startup.
"""
import json
import os

from flask import Blueprint, jsonify, request

tracking_api = Blueprint('tracking_api', __name__)

TRACKING_DEFAULTS = {
    'max_age': 30,
    'max_history': 50,
    'cleanup_interval': 10,
    'line_thickness': 2,
    'line_length': 20,
}

# Short UI keys -> DetectionTracker.update_tracking_config keys
_DETECTOR_KEYS = {
    'max_age': 'max_track_age',
    'max_history': 'max_track_history',
    'cleanup_interval': 'track_cleanup_interval',
    'line_thickness': 'track_line_thickness',
    'line_length': 'track_line_length',
}


def _sanitize(data: dict) -> dict:
    """Keep only known integer settings."""
    out = {}
    for key in TRACKING_DEFAULTS:
        if key in (data or {}):
            try:
                out[key] = int(data[key])
            except (TypeError, ValueError):
                continue
    return out


def _save_tracking_config(config: dict) -> None:
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    payload = json.dumps(config)
    upsert_config_exports(get_repo_root(), {'TRACKING_CONFIG': payload})
    os.environ['TRACKING_CONFIG'] = payload


def _apply_to_detector(detector, settings: dict) -> None:
    detector.update_tracking_config({
        _DETECTOR_KEYS[k]: v for k, v in settings.items() if k in _DETECTOR_KEYS
    })


def _effective_settings(config: dict, cam_id: int) -> dict:
    merged = dict(TRACKING_DEFAULTS)
    merged.update(_sanitize(config.get('global', {})))
    merged.update(_sanitize(config.get('cameras', {}).get(str(cam_id), {})))
    return merged


@tracking_api.route('/config', methods=['GET'])
def get_tracking_config():
    """Get tracking configuration and per-camera status (page load)."""
    try:
        from basebuddy.modules.config import load_tracking_config, CAM_URLS
        import basebuddy.modules.state as shared_state

        stored = load_tracking_config()
        global_cfg = dict(TRACKING_DEFAULTS)
        global_cfg.update(_sanitize(stored.get('global', {})))

        camera_ids = [i for i, url in enumerate(CAM_URLS) if url] or list(range(4))

        cameras = {}
        status_info = {}
        for cam_id in camera_ids:
            cameras[str(cam_id)] = _effective_settings(stored, cam_id)
            detector = shared_state.detectors.get(cam_id)
            status_info[str(cam_id)] = {
                'status': 'active' if detector is not None else 'inactive',
                'track_count': len(getattr(detector, 'track_history', {}) or {}) if detector else 0,
            }

        return jsonify({
            'ok': True,
            'config': {'global': global_cfg, 'cameras': cameras},
            'status_info': status_info,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@tracking_api.route('/config/global', methods=['POST'])
def update_global_tracking_config():
    """Update global tracking configuration."""
    try:
        from basebuddy.modules.config import load_tracking_config
        import basebuddy.modules.state as shared_state

        settings = _sanitize(request.get_json() or {})
        config = load_tracking_config()
        config['global'] = settings
        _save_tracking_config(config)

        for cam_id, detector in shared_state.detectors.items():
            _apply_to_detector(detector, _effective_settings(config, cam_id))

        return jsonify({
            'ok': True,
            'message': 'Global tracking config updated'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@tracking_api.route('/config/<int:cam_id>', methods=['POST'])
@tracking_api.route('/config/camera/<int:cam_id>', methods=['POST'])
def update_camera_tracking_config(cam_id):
    """Update per-camera tracking configuration."""
    try:
        from basebuddy.modules.config import load_tracking_config
        import basebuddy.modules.state as shared_state

        settings = _sanitize(request.get_json() or {})
        config = load_tracking_config()
        config.setdefault('cameras', {})[str(cam_id)] = settings
        _save_tracking_config(config)

        detector = shared_state.detectors.get(cam_id)
        if detector is not None:
            _apply_to_detector(detector, _effective_settings(config, cam_id))

        return jsonify({
            'ok': True,
            'message': f'Tracking config updated for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@tracking_api.route('/clear/<int:cam_id>', methods=['POST'])
def clear_camera_tracks(cam_id):
    """Clear tracking data for a camera."""
    try:
        import basebuddy.modules.state as shared_state

        if cam_id in shared_state.detectors:
            detector = shared_state.detectors[cam_id]
            if hasattr(detector, 'clear_tracks'):
                detector.clear_tracks()

        return jsonify({
            'ok': True,
            'message': f'Tracks cleared for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@tracking_api.route('/reset/<int:cam_id>', methods=['POST'])
def reset_camera_tracker(cam_id):
    """Completely reset the tracker for a camera."""
    try:
        import basebuddy.modules.state as shared_state

        detector = shared_state.detectors.get(cam_id)
        if detector is not None and hasattr(detector, 'reset_tracker'):
            detector.reset_tracker()

        return jsonify({
            'ok': True,
            'message': f'Tracker reset for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@tracking_api.route('/status', methods=['GET'])
def get_tracking_status():
    """Get tracking status for all cameras."""
    try:
        import basebuddy.modules.state as shared_state

        status = {}
        for cam_id, detector in shared_state.detectors.items():
            track_count = len(getattr(detector, 'tracks', {}))
            status[cam_id] = {
                'active_tracks': track_count,
                'enabled': True
            }

        return jsonify({
            'ok': True,
            'data': status
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500
