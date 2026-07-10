"""
Storage management API endpoints.

Provides endpoints for storage statistics and cleanup operations.
"""
from flask import Blueprint, jsonify
from basebuddy.core.services.storage_service import StorageService

storage_api = Blueprint('storage_api', __name__)


def get_storage_service():
    """Get configured storage service instance."""
    from basebuddy.modules.config import RECORD_ROOT, RETENTION_DAYS
    return StorageService(RECORD_ROOT, RETENTION_DAYS)


@storage_api.route('/stats', methods=['GET'])
def get_storage_stats():
    """
    Get storage statistics.
    
    Returns JSON with:
    - Recording storage usage
    - Disk usage
    - Retention settings
    """
    try:
        service = get_storage_service()
        stats = service.get_storage_stats()
        
        return jsonify({
            'ok': True,
            'data': stats
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@storage_api.route('/cleanup', methods=['POST'])
def run_cleanup():
    """
    Manually trigger cleanup of old recordings.
    
    Deletes recordings older than the configured retention period.
    
    Returns JSON with:
    - deleted_count: Number of files deleted
    - freed_space_mb: Space freed in MB
    """
    try:
        service = get_storage_service()
        result = service.cleanup_old_recordings()
        
        return jsonify({
            'ok': True,
            'data': result
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


