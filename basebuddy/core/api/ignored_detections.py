"""
Ignored detections API.

Manages zones where detections should be ignored (e.g., trees, bushes that
trigger false positives). Persisted as the IGNORED_DETECTIONS export in
config.txt (keyed "camera_<id>"), which is what the detector reads.
"""
import json
import os

from flask import Blueprint, jsonify, request

ignored_api = Blueprint('ignored_api', __name__)


def _save_ignored(ignored: dict) -> None:
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    payload = json.dumps(ignored)
    upsert_config_exports(get_repo_root(), {'IGNORED_DETECTIONS': payload})
    os.environ['IGNORED_DETECTIONS'] = payload


def _refresh_detector(cam_id: int, ignored: dict) -> None:
    import basebuddy.modules.state as shared_state

    detector = shared_state.detectors.get(cam_id)
    if detector is not None:
        detector.ignored_detections = ignored.get(f'camera_{cam_id}', [])


@ignored_api.route('/', methods=['GET'])
def get_ignored_detections():
    """Get all ignored detection zones."""
    try:
        from basebuddy.modules.config import reload_ignored_detections
        return jsonify({
            'ok': True,
            'data': reload_ignored_detections()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@ignored_api.route('/<int:cam_id>', methods=['POST'])
def add_ignored_detection(cam_id):
    """Add an ignored detection zone for a camera."""
    try:
        from basebuddy.modules.config import reload_ignored_detections

        data = request.get_json()
        ignored = reload_ignored_detections()
        ignored.setdefault(f'camera_{cam_id}', []).append(data)
        _save_ignored(ignored)
        _refresh_detector(cam_id, ignored)

        return jsonify({
            'ok': True,
            'message': f'Ignored zone added for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@ignored_api.route('/<int:cam_id>/<int:index>', methods=['DELETE'])
def remove_ignored_detection(cam_id, index):
    """Remove a specific ignored detection zone."""
    try:
        from basebuddy.modules.config import reload_ignored_detections

        ignored = reload_ignored_detections()
        cam_key = f'camera_{cam_id}'
        if cam_key in ignored and 0 <= index < len(ignored[cam_key]):
            ignored[cam_key].pop(index)
            _save_ignored(ignored)
            _refresh_detector(cam_id, ignored)
            return jsonify({
                'ok': True,
                'message': f'Ignored zone removed for camera {cam_id + 1}'
            })
        return jsonify({
            'ok': False,
            'error': 'Invalid index'
        }), 404
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@ignored_api.route('/<int:cam_id>', methods=['DELETE'])
def clear_ignored_detections(cam_id):
    """Clear all ignored detection zones for a camera."""
    try:
        from basebuddy.modules.config import reload_ignored_detections

        ignored = reload_ignored_detections()
        if ignored.pop(f'camera_{cam_id}', None) is not None:
            _save_ignored(ignored)
            _refresh_detector(cam_id, ignored)

        return jsonify({
            'ok': True,
            'message': f'All ignored zones cleared for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500
