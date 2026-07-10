"""
System metrics API endpoints.

Provides metrics data for system monitoring dashboards:
- Detection statistics
- Storage usage
- System resources (CPU, memory, disk)
- Camera status
"""
from flask import Blueprint, jsonify
from datetime import datetime
import psutil
import os

metrics_api = Blueprint('metrics_api', __name__)


def get_system_metrics(grabbers, analytics_db, cam_urls, calculate_recording_size_func):
    """
    Get comprehensive system metrics.
    
    Args:
        grabbers: Dictionary of camera grabbers
        analytics_db: Analytics database instance
        cam_urls: Dictionary of camera URLs
        calculate_recording_size_func: Function to calculate recording size
        
    Returns:
        Dictionary with all metrics data
    """
    # Get today's stats from database
    today_stats = analytics_db.get_today_stats()
    
    # Get hourly stats for today
    hourly_stats = analytics_db.get_hourly_stats()
    
    # Calculate storage metrics
    recording_size_bytes = calculate_recording_size_func()
    recording_size_mb = recording_size_bytes / (1024 * 1024)
    recording_size_gb = recording_size_mb / 1024
    
    # Get system info
    system_info = {
        'cpu_percent': psutil.cpu_percent(interval=1),
        'memory_percent': psutil.virtual_memory().percent,
        'memory_used_gb': round(psutil.virtual_memory().used / (1024**3), 2),
        'memory_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
        'disk_free_gb': round(psutil.disk_usage('/').free / (1024**3), 2),
        'disk_total_gb': round(psutil.disk_usage('/').total / (1024**3), 2)
    }
    
    # Get camera status (cam_urls is list from config, index = cam_id)
    camera_status = {}
    for cam_id in range(len(cam_urls)):
        url = cam_urls[cam_id] if cam_id < len(cam_urls) else None
        if url:
            grabber = grabbers.get(cam_id)
            if grabber and grabber.is_alive():
                camera_status[cam_id] = "Online"
            else:
                camera_status[cam_id] = "Offline"
        else:
            camera_status[cam_id] = "Not configured"
    
    return {
        'today_stats': today_stats,
        'hourly_stats': hourly_stats,
        'storage': {
            'recordings_mb': round(recording_size_mb, 2),
            'recordings_gb': round(recording_size_gb, 2)
        },
        'system': system_info,
        'cameras': camera_status,
        'timestamp': datetime.now().isoformat(),
        'timestamp_formatted': datetime.now().strftime("%B %d, %Y at %I:%M %p")
    }


@metrics_api.route('/system', methods=['GET'])
def get_metrics():
    """
    Get system metrics.
    
    Returns JSON with:
    - Today's detection statistics
    - Hourly statistics
    - Storage usage
    - System resources
    - Camera status
    """
    try:
        # Import shared state
        import basebuddy.modules.state as shared_state
        from basebuddy.modules.config import CAM_URLS
        
        # Import storage calculation function
        # We'll need to refactor this later
        def calculate_recording_size():
            """Calculate total size of recordings."""
            from basebuddy.modules.config import RECORD_ROOT
            total_size = 0
            if os.path.exists(RECORD_ROOT):
                for dirpath, dirnames, filenames in os.walk(RECORD_ROOT):
                    for f in filenames:
                        fp = os.path.join(dirpath, f)
                        try:
                            total_size += os.path.getsize(fp)
                        except Exception:
                            pass
            return total_size
        
        metrics = get_system_metrics(
            shared_state.grabbers,
            shared_state.analytics_db,
            CAM_URLS,
            calculate_recording_size
        )
        
        return jsonify({
            'ok': True,
            'data': metrics
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500

