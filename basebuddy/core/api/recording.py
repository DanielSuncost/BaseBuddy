"""
Recording control API endpoints.

Provides endpoints for starting, stopping, and monitoring video recording.
"""
from flask import Blueprint, jsonify, request

recording_api = Blueprint('recording_api', __name__)


@recording_api.route('/<int:cam_id>/start', methods=['POST'])
def start_recording(cam_id):
    """
    Start recording on a camera.
    
    Args:
        cam_id: Camera ID
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if cam_id not in shared_state.grabbers:
            return jsonify({
                'ok': False,
                'error': f'Camera {cam_id} not found'
            }), 404
        
        grabber = shared_state.grabbers[cam_id]
        grabber.start_recording()
        
        return jsonify({
            'ok': True,
            'message': f'Recording started on camera {cam_id}'
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@recording_api.route('/<int:cam_id>/stop', methods=['POST'])
def stop_recording(cam_id):
    """
    Stop recording on a camera.
    
    Args:
        cam_id: Camera ID
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if cam_id not in shared_state.grabbers:
            return jsonify({
                'ok': False,
                'error': f'Camera {cam_id} not found'
            }), 404
        
        grabber = shared_state.grabbers[cam_id]
        grabber.stop_recording()
        
        return jsonify({
            'ok': True,
            'message': f'Recording stopped on camera {cam_id}'
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@recording_api.route('/<int:cam_id>/status', methods=['GET'])
def get_recording_status(cam_id):
    """
    Get recording status for a camera.
    
    Args:
        cam_id: Camera ID
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if cam_id not in shared_state.grabbers:
            return jsonify({
                'ok': False,
                'error': f'Camera {cam_id} not found'
            }), 404
        
        grabber = shared_state.grabbers[cam_id]
        is_recording = grabber.is_recording() if hasattr(grabber, 'is_recording') else False
        
        status = {
            'cam_id': cam_id,
            'recording': is_recording,
            'frame_skip_rate': getattr(grabber, 'frame_skip_rate', 1)
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


@recording_api.route('/status', methods=['GET'])
def get_all_recording_status():
    """
    Get recording status for all cameras.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        statuses = {}
        for cam_id, grabber in shared_state.grabbers.items():
            is_recording = grabber.is_recording() if hasattr(grabber, 'is_recording') else False
            statuses[cam_id] = {
                'cam_id': cam_id,
                'recording': is_recording,
                'frame_skip_rate': getattr(grabber, 'frame_skip_rate', 1)
            }
        
        return jsonify({
            'ok': True,
            'data': statuses
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@recording_api.route('/<int:cam_id>/frame-skip', methods=['POST'])
def set_frame_skip_rate(cam_id):
    """
    Set frame skip rate for a camera's recording.
    
    Args:
        cam_id: Camera ID
    
    Body:
        rate: Frame skip rate (integer)
    """
    try:
        import basebuddy.modules.state as shared_state
        
        if cam_id not in shared_state.grabbers:
            return jsonify({
                'ok': False,
                'error': f'Camera {cam_id} not found'
            }), 404
        
        data = request.get_json()
        rate = int(data.get('rate', 1))
        
        if rate < 1:
            return jsonify({
                'ok': False,
                'error': 'Frame skip rate must be >= 1'
            }), 400
        
        grabber = shared_state.grabbers[cam_id]
        grabber.frame_skip_rate = rate
        
        return jsonify({
            'ok': True,
            'message': f'Frame skip rate set to {rate} for camera {cam_id}'
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


