"""
Camera Detail page routes.

Single camera inference view with bounding boxes and controls.
"""
from flask import render_template
from basebuddy.pages.camera_detail import camera_detail_bp


@camera_detail_bp.route('/camera/<int:cam_id>')
def camera_inference_view(cam_id: int):
    """Single camera inference view with bounding boxes and controls"""
    
    # Get camera info
    cam_name = f"Camera {cam_id + 1}"
    try:
        from basebuddy.modules.camera_profiles import get_profile_manager
        manager = get_profile_manager()
        profile = manager.get_profile(cam_id)
        if profile and profile.name:
            cam_name = profile.name
    except Exception:
        pass
    
    return render_template('camera_detail.html',
                          active_page='dashboard',
                          cam_id=cam_id,
                          cam_name=cam_name)
