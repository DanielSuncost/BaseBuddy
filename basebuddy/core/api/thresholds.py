"""
Detection threshold configuration API.

Per-camera, per-class confidence thresholds are persisted as the
CLASS_THRESHOLDS export in config.txt (keyed "camera_<id>"), which is the
source the detector reads, and applied to live detectors on save.
"""
import json
import os

from flask import Blueprint, jsonify, request

thresholds_api = Blueprint('thresholds_api', __name__)


def _save_thresholds(all_thresholds: dict) -> None:
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    payload = json.dumps(all_thresholds)
    upsert_config_exports(get_repo_root(), {'CLASS_THRESHOLDS': payload})
    os.environ['CLASS_THRESHOLDS'] = payload


@thresholds_api.route('/', methods=['GET'])
def get_all_thresholds():
    """Get threshold configuration for all cameras."""
    try:
        from basebuddy.modules.config import reload_class_thresholds
        return jsonify({
            'ok': True,
            'data': reload_class_thresholds()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@thresholds_api.route('/camera/<int:cam_id>', methods=['POST'])
def update_camera_thresholds(cam_id):
    """Update detection thresholds for a camera."""
    try:
        from basebuddy.modules.config import reload_class_thresholds

        data = request.get_json() or {}
        thresholds = {}
        for cls, value in data.items():
            try:
                thresholds[str(cls)] = float(value)
            except (TypeError, ValueError):
                continue

        all_thresholds = reload_class_thresholds()
        all_thresholds[f'camera_{cam_id}'] = thresholds
        _save_thresholds(all_thresholds)

        # Apply to the running detector immediately
        import basebuddy.modules.state as shared_state
        detector = shared_state.detectors.get(cam_id)
        if detector is not None:
            detector.class_thresholds = dict(thresholds)

        return jsonify({
            'ok': True,
            'message': f'Thresholds updated for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@thresholds_api.route('/camera/<int:cam_id>/reset', methods=['POST'])
def reset_camera_thresholds(cam_id):
    """Reset thresholds to defaults for a camera."""
    try:
        from basebuddy.modules.config import reload_class_thresholds

        all_thresholds = reload_class_thresholds()
        all_thresholds.pop(f'camera_{cam_id}', None)
        _save_thresholds(all_thresholds)

        import basebuddy.modules.state as shared_state
        detector = shared_state.detectors.get(cam_id)
        if detector is not None:
            detector.reset_class_thresholds()

        return jsonify({
            'ok': True,
            'message': f'Thresholds reset for camera {cam_id + 1}'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500
