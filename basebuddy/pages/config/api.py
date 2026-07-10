"""Config page API — inference backend settings."""
from flask import jsonify, request

from basebuddy.pages.config import config_bp


@config_bp.route('/api/config/inference', methods=['GET'])
def get_inference_config():
    from basebuddy.modules.config import (
        INFERENCE_CLOUD_API_KEY,
        INFERENCE_CLOUD_ENDPOINT,
        INFERENCE_HYBRID_FALLBACK,
        INFERENCE_MODE,
    )
    return jsonify({
        'ok': True,
        'data': {
            'mode': INFERENCE_MODE,
            'hybrid_fallback': INFERENCE_HYBRID_FALLBACK,
            'cloud_endpoint': INFERENCE_CLOUD_ENDPOINT,
            'cloud_api_key_set': bool(INFERENCE_CLOUD_API_KEY),
            'cloud_api_key_mask': ('****' + INFERENCE_CLOUD_API_KEY[-4:]) if len(INFERENCE_CLOUD_API_KEY or '') > 4 else '',
        },
    })


@config_bp.route('/api/config/inference', methods=['POST'])
def save_inference_config():
    import os
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    data = request.get_json(force=True) or {}
    updates = {}
    if 'mode' in data:
        mode = str(data['mode']).lower()
        if mode in ('local', 'cloud', 'hybrid'):
            updates['INFERENCE_MODE'] = mode
    if 'hybrid_fallback' in data:
        updates['INFERENCE_HYBRID_FALLBACK'] = 'true' if data['hybrid_fallback'] else 'false'
    if 'cloud_endpoint' in data:
        updates['INFERENCE_CLOUD_ENDPOINT'] = str(data['cloud_endpoint']).strip()
    key = data.get('cloud_api_key')
    if key and str(key).strip() and not str(key).startswith('****'):
        updates['INFERENCE_CLOUD_API_KEY'] = str(key).strip()

    if not updates:
        return jsonify({'ok': False, 'error': 'No changes'}), 400

    upsert_config_exports(get_repo_root(), updates)
    for k, v in updates.items():
        os.environ[k] = v

    return jsonify({'ok': True, 'message': 'Inference settings saved. Restart recommended for detection workers.'})
