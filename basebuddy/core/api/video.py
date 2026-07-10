"""
Video streaming API endpoints.

Provides MJPEG video streams with and without ML annotations.
"""
from flask import Blueprint, Response

video_api = Blueprint('video_api', __name__)


@video_api.route('/<int:cam_id>')
def video_stream(cam_id):
    """
    MJPEG video stream with ML annotations.
    
    Args:
        cam_id: Camera ID
        
    Returns:
        Multipart MJPEG stream
    """
    import basebuddy.modules.state as shared_state
    from basebuddy.modules.config import JPEG_QUALITY
    from basebuddy.core.services.video_streaming_service import mjpeg_generator
    
    return Response(
        mjpeg_generator(cam_id, shared_state.grabbers, JPEG_QUALITY),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@video_api.route('/raw/<int:cam_id>')
def video_raw_stream(cam_id):
    """
    Raw MJPEG video stream without ML annotations.
    
    Lower latency stream for camera wall.
    
    Args:
        cam_id: Camera ID
        
    Returns:
        Multipart MJPEG stream
    """
    import basebuddy.modules.state as shared_state
    from basebuddy.modules.config import JPEG_QUALITY
    from basebuddy.core.services.video_streaming_service import mjpeg_raw_generator
    
    return Response(
        mjpeg_raw_generator(cam_id, shared_state.grabbers, JPEG_QUALITY, target_fps=15),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


