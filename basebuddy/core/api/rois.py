"""
Region API (stored as IGNORED_ROIS in config for backward compatibility).

Regions support polygons, labels, filter modes, analytics tagging, and notifications.
"""
from flask import Blueprint, jsonify, request

from basebuddy.core.regions import load_camera_regions, migrate_legacy_roi, normalize_region
from basebuddy.core.services.region_notifications import get_region_notification_service

rois_api = Blueprint('rois_api', __name__)


def _roi_response(cam_id: int, regions: list):
    return jsonify({
        'ok': True,
        'camera': cam_id,
        'rois': regions,
        'regions': regions,
        'data': {'camera': cam_id, 'rois': regions, 'regions': regions},
    })


def _persist_rois(all_rois: dict) -> None:
    import json
    import basebuddy.modules.config as config_module
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    upsert_config_exports(get_repo_root(), {'IGNORED_ROIS': json.dumps(all_rois)})
    config_module.IGNORED_ROIS = all_rois


def _normalize_payload_list(data: dict) -> list:
    raw = data.get('regions') or data.get('rois') or []
    if not isinstance(raw, list):
        return []
    return [normalize_region(migrate_legacy_roi(r)) for r in raw if isinstance(r, dict)]


@rois_api.route('/notifications/recent', methods=['GET'])
def recent_region_notifications():
    try:
        cam = request.args.get('camera')
        cam_id = int(cam) if cam not in (None, '') else None
        limit = min(100, int(request.args.get('limit', 30)))
        svc = get_region_notification_service()
        return jsonify({'ok': True, 'notifications': svc.recent(limit=limit, camera_id=cam_id)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@rois_api.route('/<int:cam_id>', methods=['GET', 'POST'])
def camera_rois(cam_id):
    """Get or set labeled regions for a camera."""
    try:
        from basebuddy.modules.config import reload_ignored_rois

        if request.method == 'GET':
            regions = load_camera_regions(cam_id)
            return _roi_response(cam_id, regions)

        data = request.get_json(force=True) or {}
        regions_data = _normalize_payload_list(data)

        existing = reload_ignored_rois()
        existing[f'camera_{cam_id}'] = regions_data
        _persist_rois(existing)

        return jsonify({
            'ok': True,
            'message': 'Regions saved',
            'camera': cam_id,
            'count': len(regions_data),
            'rois': regions_data,
            'regions': regions_data,
            'data': {
                'camera': cam_id,
                'count': len(regions_data),
                'rois': regions_data,
                'regions': regions_data,
            },
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
