"""
Resource Management Routes - UI and API for resource monitoring and configuration
"""
import logging
from typing import Optional
from flask import Blueprint, jsonify, render_template, request
from basebuddy.modules.resource_manager import get_resource_manager, ResourcePriority
from basebuddy.modules.resource_monitor import get_resource_monitor
from basebuddy.modules.adaptive_rate_limiter import AdaptiveRateLimiter

logger = logging.getLogger(__name__)

resources_bp = Blueprint('resources', __name__)

# Global adaptive rate limiter
_adaptive_limiter: Optional[AdaptiveRateLimiter] = None

def get_adaptive_limiter() -> AdaptiveRateLimiter:
    """Get or create adaptive rate limiter"""
    global _adaptive_limiter
    if _adaptive_limiter is None:
        resource_manager = get_resource_manager()
        _adaptive_limiter = AdaptiveRateLimiter(resource_manager)
    return _adaptive_limiter


@resources_bp.route('/resources')
def resources_page():
    """Resource monitoring and configuration page"""
    return render_template('resources.html', active_page='resources')


@resources_bp.route('/api/resources/status')
def api_resource_status():
    """Get current resource status"""
    try:
        resource_manager = get_resource_manager()
        status = resource_manager.get_resource_status()
        return jsonify(status)
    except Exception as e:
        logger.exception("resources API request failed")
        return jsonify({
            'error': str(e),
        }), 500


@resources_bp.route('/api/resources/config', methods=['POST'])
def api_update_resource_config():
    """Update resource management configuration"""
    try:
        data = request.get_json()
        resource_manager = get_resource_manager()
        adaptive_limiter = get_adaptive_limiter()
        
        # Update resource manager config
        resource_manager.update_config(data)
        
        # Update adaptive limiter config
        adaptive_limiter.update_config(data)
        
        return jsonify({'ok': True})
    except Exception as e:
        logger.exception("resources API request failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500


@resources_bp.route('/api/profiling/cameras')
def api_profiling_cameras():
    """Get per-camera performance metrics"""
    try:
        from basebuddy.modules.profiler import get_profiler
        profiler = get_profiler()
        metrics = profiler.get_all_metrics()
        bottlenecks = profiler.identify_bottlenecks()
        summary = profiler.get_summary()
        
        return jsonify({
            'ok': True,
            'metrics': metrics,
            'bottlenecks': bottlenecks,
            'summary': summary
        })
    except Exception as e:
        logger.exception("resources API request failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500


@resources_bp.route('/api/profiling/camera/<int:camera_id>')
def api_profiling_camera(camera_id):
    """Get performance metrics for a specific camera"""
    try:
        from basebuddy.modules.profiler import get_profiler
        profiler = get_profiler()
        metrics = profiler.get_camera_metrics(camera_id)
        bottlenecks = profiler.identify_bottlenecks()
        
        return jsonify({
            'ok': True,
            'camera_id': camera_id,
            'metrics': metrics,
            'bottlenecks': bottlenecks.get(camera_id, [])
        })
    except Exception as e:
        logger.exception("resources API request failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500


@resources_bp.route('/api/profiling/summary')
def api_profiling_summary():
    """Get summary of all performance metrics"""
    try:
        from basebuddy.modules.profiler import get_profiler
        profiler = get_profiler()
        summary = profiler.get_summary()
        
        return jsonify({
            'ok': True,
            'summary': summary
        })
    except Exception as e:
        logger.exception("resources API request failed")
        return jsonify({
            'ok': False,
            'error': str(e),
        }), 500

