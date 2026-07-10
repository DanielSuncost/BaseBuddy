"""
WebSocket handlers for camera streaming.

Provides real-time video streaming via WebSockets to support unlimited simultaneous camera views.
"""
import base64
import time
import cv2
from threading import Thread, Event, Lock
import basebuddy.modules.state as shared_state
import logging

logger = logging.getLogger(__name__)

# Track connected clients and their camera subscriptions
ws_clients = {}  # sid -> set of camera IDs
ws_clients_lock = Lock()  # guards ws_clients and its sets (handlers vs broadcast thread)
ws_broadcast_thread = None
ws_stop_event = Event()


def register_socketio_handlers(socketio, app):
    """
    Register SocketIO event handlers for camera streaming.
    
    Args:
        socketio: SocketIO instance
        app: Flask app instance
    """
    
    @socketio.on('connect')
    def ws_on_connect():
        """Client connected via WebSocket"""
        from flask import request
        with ws_clients_lock:
            ws_clients[request.sid] = set()
        logger.info(f"[WS] Client connected: {request.sid}")
    
    @socketio.on('disconnect')
    def ws_on_disconnect():
        """Client disconnected"""
        from flask import request
        with ws_clients_lock:
            ws_clients.pop(request.sid, None)
        logger.info(f"[WS] Client disconnected: {request.sid}")
    
    @socketio.on('subscribe')
    def ws_subscribe(data):
        """Client subscribes to camera feeds"""
        from flask import request
        cam_ids = data.get('cameras', [])
        with ws_clients_lock:
            known = request.sid in ws_clients
            if known:
                ws_clients[request.sid] = set(cam_ids)
        if known:
            logger.info(f"[WS] Client {request.sid} subscribed to cameras: {cam_ids}")
            # Start broadcast thread if not running
            start_ws_broadcast(socketio)
    
    @socketio.on('unsubscribe')
    def ws_unsubscribe(data):
        """Client unsubscribes from camera feeds"""
        from flask import request
        cam_ids = data.get('cameras', [])
        with ws_clients_lock:
            if request.sid in ws_clients:
                ws_clients[request.sid] = ws_clients[request.sid] - set(cam_ids)


def start_ws_broadcast(socketio):
    """Start the WebSocket broadcast thread"""
    global ws_broadcast_thread
    if ws_broadcast_thread is None or not ws_broadcast_thread.is_alive():
        ws_stop_event.clear()
        ws_broadcast_thread = Thread(target=lambda: ws_broadcast_loop(socketio), daemon=True)
        ws_broadcast_thread.start()
        logger.info("[WS] Broadcast thread started")


def ws_broadcast_loop(socketio):
    """Background thread that broadcasts frames to all subscribed clients"""
    target_fps = 12  # Frames per second per camera
    frame_interval = 1.0 / target_fps
    last_frame_time = {}
    last_frame_warn = {}  # Track when we last warned about no frames
    
    while not ws_stop_event.is_set():
        try:
            # Snapshot subscriptions under the lock
            with ws_clients_lock:
                subscriptions = {sid: set(cams) for sid, cams in ws_clients.items()}
            all_wanted_cams = set()
            for cams in subscriptions.values():
                all_wanted_cams.update(cams)
            
            if not all_wanted_cams:
                time.sleep(0.1)
                continue
            
            current_time = time.time()
            
            # Get frames for each wanted camera
            for cam_id in all_wanted_cams:
                # Rate limit per camera
                if cam_id in last_frame_time:
                    if current_time - last_frame_time[cam_id] < frame_interval:
                        continue
                
                if cam_id not in shared_state.grabbers:
                    continue
                
                grabber = shared_state.grabbers[cam_id]
                # Wall frames carry lightweight detection overlays (boxes +
                # track trails) drawn from cached results, so tiles keep the
                # full stream frame rate even when detection runs slowly.
                frame, ts = grabber.get_latest_wall_frame()
                
                if frame is None:
                    # Warn about missing frames (but not too often)
                    warn_key = f"no_frame_{cam_id}"
                    if warn_key not in last_frame_warn or current_time - last_frame_warn[warn_key] > 30:
                        logger.info(f"[WS] Camera {cam_id}: No frames available for broadcast")
                        last_frame_warn[warn_key] = current_time
                    continue
                
                # Resize for efficiency (max 640px width for wall view)
                h, w = frame.shape[:2]
                if w > 640:
                    scale = 640 / w
                    new_w = 640
                    new_h = int(h * scale)
                    frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
                
                # Encode to JPEG
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                
                # Broadcast to all clients subscribed to this camera
                for sid, cams in subscriptions.items():
                    if cam_id in cams:
                        try:
                            socketio.emit('frame', {'cam_id': cam_id, 'frame': frame_b64}, room=sid)
                        except Exception as e:
                            logger.error(f"[WS] Error sending frame to {sid}: {e}")
                
                last_frame_time[cam_id] = current_time
            
            # Small sleep to prevent busy loop
            time.sleep(0.01)
            
        except Exception as e:
            logger.error(f"[WS] Error in broadcast loop: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)  # Back off on error
    
    logger.info("[WS] Broadcast thread stopped")

