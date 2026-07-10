"""
Traffic analytics API endpoints.

Provides traffic monitoring and analysis data.
"""
from flask import Blueprint, jsonify, request

traffic_api = Blueprint('traffic_api', __name__)


@traffic_api.route('/sources', methods=['GET'])
def get_traffic_sources():
    """
    List cameras usable as traffic sources.

    For every configured camera: whether traffic capture is enabled (it has
    analytics regions or is the legacy TRAFFIC_CAM_ID), its analytics region
    labels, and the object classes / track counts recorded so far.
    """
    try:
        import basebuddy.modules.state as shared_state
        from basebuddy.core.regions import load_camera_regions
        from basebuddy.modules.camera_profiles import get_profile_manager
        from basebuddy.modules.config import CAM_URLS, TRAFFIC_CAM_ID

        db_sources = shared_state.analytics_db.get_traffic_sources()
        stats = {}  # cam_id -> {'regions': set, 'classes': set, 'count': int}
        for s in db_sources:
            entry = stats.setdefault(s['camera_id'],
                                     {'regions': set(), 'classes': set(), 'count': 0})
            if s['region_label']:
                entry['regions'].add(s['region_label'])
            if s['class_name']:
                entry['classes'].add(s['class_name'])
            entry['count'] += s['track_count']

        try:
            profiles = get_profile_manager().get_all_profiles()
        except Exception:
            profiles = {}

        cameras = []
        for cam_id, url in enumerate(CAM_URLS):
            if not url and cam_id not in stats:
                continue
            regions = load_camera_regions(cam_id)
            analytics_labels = sorted({(r.get('label') or '').strip()
                                       for r in regions
                                       if r.get('analytics') and (r.get('label') or '').strip()})
            db_entry = stats.get(cam_id, {'regions': set(), 'classes': set(), 'count': 0})
            profile = profiles.get(cam_id)
            cameras.append({
                'id': cam_id,
                'name': (profile.name if profile and profile.name else f'Camera {cam_id + 1}'),
                'enabled': bool(analytics_labels) or cam_id == TRAFFIC_CAM_ID,
                'region_labels': sorted(set(analytics_labels) | db_entry['regions']),
                'classes': sorted(db_entry['classes']),
                'track_count': db_entry['count'],
            })

        return jsonify({
            'ok': True,
            'cameras': cameras,
            'default_cam': TRAFFIC_CAM_ID if TRAFFIC_CAM_ID >= 0 else None,
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@traffic_api.route('/hourly', methods=['GET'])
def get_traffic_hourly():
    """
    Get hourly traffic statistics.
    
    Query params:
    - date: Date string (optional)
    - cam: Camera ID (optional)
    - region: Region label (optional)
    - class: Object class (optional)
    
    Returns hourly vehicle counts.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        date = request.args.get('date')
        cam = request.args.get('cam')
        region = request.args.get('region') or request.args.get('label')
        klass = request.args.get('class')
        cam_id = int(cam) if cam is not None and cam != '' else None
        
        data = shared_state.analytics_db.get_traffic_hourly_stats(
            date, cam_id, region_label=region or None, class_name=klass or None)
        
        return jsonify({
            'ok': True,
            'data': data
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


@traffic_api.route('/directions', methods=['GET'])
def get_traffic_directions():
    """
    Get traffic direction statistics.
    
    Query params:
    - date: Date string (optional)
    - cam: Camera ID (optional)
    - bin: Bin size in degrees (default 45)
    
    Returns traffic flow by direction.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        date = request.args.get('date')
        cam = request.args.get('cam')
        bin_size = int(request.args.get('bin', '45'))
        region = request.args.get('region') or request.args.get('label')
        klass = request.args.get('class')
        cam_id = int(cam) if cam is not None and cam != '' else None
        
        data = shared_state.analytics_db.get_traffic_direction_stats(
            date, cam_id, bin_size, region_label=region or None, class_name=klass or None,
        )
        
        return jsonify({
            'ok': True,
            'data': data
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


def _direction_color(direction_deg: float):
    """Map a travel direction (deg, 0=east/90=north) to a saturated BGR color."""
    import numpy as np
    import cv2
    hue = int((direction_deg % 360.0) / 2.0)  # OpenCV hue range is 0-179
    hsv = np.uint8([[[hue, 255, 255]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0][0]
    return int(b), int(g), int(r)


@traffic_api.route('/flow-map', methods=['GET'])
def get_traffic_flow_map():
    """
    Render a camera frame with all track paths from a time window drawn on it.

    Query params:
    - cam: Camera ID (required)
    - minutes: Window length ending now (default 60)
    - region: Region label (optional)
    - class: Object class (optional)

    Returns a JPEG. Each track is a polyline colored by its direction of
    travel with an arrowhead at the end.
    """
    try:
        import time
        import cv2
        import numpy as np
        from flask import Response
        import basebuddy.modules.state as shared_state

        cam = request.args.get('cam')
        if cam is None or cam == '':
            return jsonify({'ok': False, 'error': 'cam is required'}), 400
        cam_id = int(cam)
        minutes = max(1.0, float(request.args.get('minutes', '60')))
        region = request.args.get('region') or None
        klass = request.args.get('class') or None

        end_ts = time.time()
        start_ts = end_ts - minutes * 60.0
        tracks = shared_state.analytics_db.get_traffic_paths(
            cam_id, start_ts, end_ts, region_label=region, class_name=klass)

        # Background: latest raw frame (tracks were recorded in raw-frame
        # pixel coordinates, before any profile rotation/flip).
        frame = None
        grabber = shared_state.grabbers.get(cam_id)
        if grabber is not None:
            with grabber.lock:
                if grabber.frames:
                    frame = grabber.frames[-1].copy()
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Dim the frame so the traces stand out
        frame = (frame.astype(np.float32) * 0.6).astype(np.uint8)

        for t in tracks:
            pts = np.array([[int(p['x']), int(p['y'])] for p in t['points']], dtype=np.int32)
            color = _direction_color(t['direction_deg'])
            cv2.polylines(frame, [pts], False, color, 1, cv2.LINE_AA)
            if len(pts) >= 2:
                cv2.arrowedLine(frame, tuple(pts[-2]), tuple(pts[-1]), color, 1,
                                cv2.LINE_AA, tipLength=0.6)

        # Compact legend: direction -> color (0=E, 90=N per atan2(-dy, dx))
        h = frame.shape[0]
        legend = [('E', 0), ('N', 90), ('W', 180), ('S', 270)]
        for i, (name, deg) in enumerate(legend):
            x = 8 + i * 42
            cv2.circle(frame, (x, h - 12), 5, _direction_color(deg), -1, cv2.LINE_AA)
            cv2.putText(frame, name, (x + 9, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (255, 255, 255), 1, cv2.LINE_AA)
        # Bottom-right so it doesn't collide with burned-in stream captions
        caption = f'{len(tracks)} tracks / last {int(minutes)} min'
        (tw, _), _ = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(frame, caption, (frame.shape[1] - tw - 8, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        ok, jpg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return jsonify({'ok': False, 'error': 'encode failed'}), 500
        return Response(jpg.tobytes(), mimetype='image/jpeg')

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@traffic_api.route('/recent', methods=['GET'])
def get_traffic_recent():
    """
    Get recent traffic tracks.
    
    Query params:
    - limit: Maximum number of tracks to return (default 50)
    
    Returns recent vehicle tracks with speed data.
    """
    try:
        import basebuddy.modules.state as shared_state
        
        cam = request.args.get('cam')
        region = request.args.get('region') or request.args.get('label')
        klass = request.args.get('class')
        if cam is not None and cam != '':
            cam_id = int(cam)
        else:
            from basebuddy.modules.config import TRAFFIC_CAM_ID
            cam_id = TRAFFIC_CAM_ID if TRAFFIC_CAM_ID >= 0 else 0
        limit = int(request.args.get('limit', '50'))
        
        data = shared_state.analytics_db.get_recent_traffic_tracks(
            cam_id, limit, region_label=region or None, class_name=klass or None,
        )
        
        return jsonify({
            'ok': True,
            'data': data
        })
    
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e)
        }), 500


