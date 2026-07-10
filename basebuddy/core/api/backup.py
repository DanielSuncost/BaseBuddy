"""
Backup management API endpoints.

Provides endpoints for backup system status and control.
"""
from flask import Blueprint, jsonify

backup_api = Blueprint('backup_api', __name__)


@backup_api.route('/status', methods=['GET'])
def get_backup_status():
    """
    Get backup system status.
    
    Returns JSON with:
    - enabled: Whether backup is enabled
    - drive_available: Whether backup drive is accessible
    - last_backup: Timestamp of last backup
    - files_backed_up: Count of backed up files
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if not shared_state.backup_manager:
            return jsonify({
                'ok': False,
                'error': 'Backup manager not initialized'
            }), 500
        
        status = shared_state.backup_manager.get_status()
        
        return jsonify({
            'ok': True,
            'data': status
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@backup_api.route('/trigger', methods=['POST'])
def trigger_backup():
    """
    Manually trigger a backup operation.
    
    Forces an immediate backup of all eligible recordings.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if not shared_state.backup_manager:
            return jsonify({
                'ok': False,
                'error': 'Backup manager not initialized'
            }), 500
        
        shared_state.backup_manager.perform_backup()
        
        return jsonify({
            'ok': True,
            'message': 'Backup triggered successfully'
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@backup_api.route('/files', methods=['GET'])
def get_backup_files():
    """
    Get list of files that have been backed up.
    
    Returns JSON with backed up file information.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if not shared_state.backup_manager:
            return jsonify({
                'ok': False,
                'error': 'Backup manager not initialized'
            }), 500
        
        files = shared_state.backup_manager.backed_up_files
        
        return jsonify({
            'ok': True,
            'data': {
                'count': len(files),
                'files': files
            }
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@backup_api.route('/drives', methods=['GET'])
def get_available_drives():
    """
    Get list of available external drives.
    
    Useful for backup drive configuration.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if not shared_state.backup_manager:
            return jsonify({
                'ok': False,
                'error': 'Backup manager not initialized'
            }), 500
        
        drives = shared_state.backup_manager.find_external_drives()
        
        return jsonify({
            'ok': True,
            'data': drives
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


