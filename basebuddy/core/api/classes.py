"""
Object class management API.

Manages available detection classes and disabled classes configuration.
Disabled classes are persisted as the DISABLED_CLASSES export in config.txt,
which is what the detector actually reads.
"""
import json
import os

from flask import Blueprint, jsonify, request

classes_api = Blueprint('classes_api', __name__)


@classes_api.route('/available', methods=['GET'])
def get_available_classes():
    """Get list of available detection classes."""
    try:
        from basebuddy.core.inference.types import COCO_CLASSES
        return jsonify({
            'ok': True,
            'data': list(COCO_CLASSES)
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@classes_api.route('/disabled', methods=['GET'])
def get_disabled_classes():
    """Get list of disabled classes."""
    try:
        from basebuddy.modules.config import reload_disabled_classes
        return jsonify({
            'ok': True,
            'data': reload_disabled_classes()
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@classes_api.route('/disabled', methods=['POST'])
def update_disabled_classes():
    """Update list of disabled classes (persist + apply to live detectors)."""
    try:
        from basebuddy.core.config_persist import upsert_config_exports
        from basebuddy.core.paths import get_repo_root

        data = request.get_json() or {}
        disabled = data.get('classes', data.get('disabled_classes', []))
        if not isinstance(disabled, list):
            return jsonify({'ok': False, 'error': 'classes must be a list'}), 400
        disabled = [str(c) for c in disabled]

        payload = json.dumps(disabled)
        upsert_config_exports(get_repo_root(), {'DISABLED_CLASSES': payload})
        os.environ['DISABLED_CLASSES'] = payload

        # Apply to running detectors immediately
        import basebuddy.modules.state as shared_state
        for detector in shared_state.detectors.values():
            try:
                detector.reload_disabled_classes()
            except Exception:
                pass

        return jsonify({
            'ok': True,
            'message': f'Disabled classes updated ({len(disabled)} disabled)'
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500
