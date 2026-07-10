"""
Video streaming service.

Provides MJPEG stream generators for camera feeds with and without ML annotations.
"""
import cv2
import numpy as np
import time
from datetime import datetime
import logging

logger = logging.getLogger('basebuddy')


def mjpeg_generator(cam_id, grabbers_dict, jpeg_quality=85):
    """
    Generate MJPEG stream for camera with ML annotations.
    
    Args:
        cam_id: Camera ID
        grabbers_dict: Dictionary of camera grabbers
        jpeg_quality: JPEG compression quality (1-100)
        
    Yields:
        MJPEG frame bytes
    """
    while True:
        if cam_id not in grabbers_dict:
            # Show placeholder
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, f"Camera {cam_id+1}: Not Configured", (50, 240),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2, cv2.LINE_AA)
        else:
            grabber = grabbers_dict[cam_id]
            frame, ts = grabber.get_latest_frame()
            
            if frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, f"Camera {cam_id+1}: Connecting...", (50, 240),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2, cv2.LINE_AA)
            else:
                # Use annotated frame if available
                with grabber.lock:
                    if hasattr(grabber, 'last_annotated_frame') and grabber.last_annotated_frame is not None:
                        frame = grabber.last_annotated_frame.copy()
                    
                    # Overlay detection count
                    if hasattr(grabber, 'last_det_count'):
                        det_count = int(getattr(grabber, 'last_det_count', 0))
                        cv2.putText(frame, f"Detections: {det_count}", (10, 50),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2, cv2.LINE_AA)
                
                # Add timestamp overlay
                txt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                cv2.putText(frame, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2, cv2.LINE_AA)
        
        # Encode and yield frame
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            time.sleep(0.1)
            continue
        
        b = jpg.tobytes()
        yield b"".join([
            b"--frame\r\n",
            b"Content-Type: image/jpeg\r\n",
            f"Content-Length: {len(b)}\r\n".encode(),
            b"\r\n",
            b,
            b"\r\n",
        ])
        
        time.sleep(0.033)  # ~30 FPS max


def mjpeg_raw_generator(cam_id, grabbers_dict, jpeg_quality=85, target_fps=15):
    """
    Generate raw MJPEG stream without ML annotations.
    
    Lower latency stream for camera wall view.
    
    Args:
        cam_id: Camera ID
        grabbers_dict: Dictionary of camera grabbers
        jpeg_quality: JPEG compression quality (1-100)
        target_fps: Target frames per second
        
    Yields:
        MJPEG frame bytes
    """
    frame_time = 1.0 / target_fps
    last_time = time.time()
    
    while True:
        # Rate limit
        elapsed = time.time() - last_time
        if elapsed < frame_time:
            time.sleep(frame_time - elapsed)
        last_time = time.time()
        
        if cam_id not in grabbers_dict:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, f"Camera {cam_id+1}: Not Configured", (50, 240),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2, cv2.LINE_AA)
        else:
            grabber = grabbers_dict[cam_id]
            frame, ts = grabber.get_latest_frame()
            
            if frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, f"Camera {cam_id+1}: Connecting...", (50, 240),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 0), 2, cv2.LINE_AA)
            else:
                # Only add timestamp - no detection overlays
                txt = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                cv2.putText(frame, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 1, cv2.LINE_AA)
        
        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
        if not ok:
            continue
        
        b = jpg.tobytes()
        yield b"".join([
            b"--frame\r\n",
            b"Content-Type: image/jpeg\r\n",
            f"Content-Length: {len(b)}\r\n".encode(),
            b"\r\n",
            b,
            b"\r\n",
        ])


