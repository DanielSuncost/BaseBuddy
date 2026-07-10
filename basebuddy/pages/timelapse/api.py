"""
Timelapse API endpoints.

Handles timelapse image listing, creation, and scheduling.
"""
import os
import json
import time
from datetime import datetime
from flask import request, jsonify
from basebuddy.pages.timelapse import timelapse_bp


from basebuddy.core.paths import get_repo_root, get_stills_root, get_timelapse_output_root

PROJECT_ROOT = get_repo_root()
STILLS_ROOT = get_stills_root()
TIMELAPSE_OUTPUT = get_timelapse_output_root()
SCHEDULES_FILE = os.path.join(PROJECT_ROOT, "timelapse_schedules.json")

from basebuddy.modules.config import ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, ARCHIVE_ENABLED
ARCHIVE_STILLS_ROOT = os.path.join(ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, "stills")
ARCHIVE_TIMELAPSE_ROOT = os.path.join(ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, "timelapse_output")


def _safe_join(root: str, rel_path: str) -> str | None:
    """Join rel_path under root, rejecting path traversal outside root."""
    full = os.path.realpath(os.path.join(root, rel_path))
    root_real = os.path.realpath(root)
    if full == root_real or full.startswith(root_real + os.sep):
        return full
    return None


def _resolve_still(rel_path: str) -> str | None:
    """Return the absolute path to a still, checking local first then archive."""
    for root in (STILLS_ROOT, ARCHIVE_STILLS_ROOT):
        full = _safe_join(root, rel_path)
        if full and os.path.isfile(full):
            return full
    return None


def _list_camera_folders() -> dict[str, list[str]]:
    """Return {folder_name: [roots_that_contain_it]} across local + archive."""
    folders: dict[str, list[str]] = {}
    for root in (STILLS_ROOT, ARCHIVE_STILLS_ROOT):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if name.startswith("camera_") and os.path.isdir(os.path.join(root, name)):
                folders.setdefault(name, []).append(root)
    return folders


def _list_jpgs_in_folder(folder_name: str, roots: list[str]) -> dict[str, str]:
    """Return {filename: absolute_path} for .jpg files, local wins on dupes."""
    files: dict[str, str] = {}
    for root in reversed(roots):
        folder = os.path.join(root, folder_name)
        if not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            if f.endswith(".jpg"):
                files[f] = os.path.join(folder, f)
    return files


# Static file serving for /stills and /timelapse_output lives in
# basebuddy/core/api/static_files.py (single owner, with archive fallback).

# ============================================================
# Image Listing API
# ============================================================

@timelapse_bp.route('/api/timelapse/images')
@timelapse_bp.route('/api/timelapse/images/<int:cam_id>')
def api_get_timelapse_images(cam_id=None):
    """Get timelapse images for a camera with filtering options"""
    try:
        start_date = request.args.get('start')
        end_date = request.args.get('end')
        start_time = request.args.get('startTime', '00:00')
        end_time = request.args.get('endTime', '23:59')
        exclude_dark = request.args.get('excludeDark', 'false').lower() == 'true'
        brightness_threshold = int(request.args.get('brightnessThreshold', '10'))
        apply_daily_time = request.args.get('applyDailyTime', 'false').lower() == 'true'
        daily_start = request.args.get('dailyStart', '06:00')
        daily_end = request.args.get('dailyEnd', '23:00')
        
        images = []
        
        all_folders = _list_camera_folders()
        if cam_id is not None:
            key = f'camera_{cam_id}'
            all_folders = {key: all_folders[key]} if key in all_folders else {}
        
        for folder, roots in all_folders.items():
            cid = int(folder.replace('camera_', ''))
            jpgs = _list_jpgs_in_folder(folder, roots)
            
            for img_file in jpgs:
                # Parse timestamp from filename (format: YYYYMMDD_HHMMSS.jpg)
                try:
                    ts_str = img_file.replace('.jpg', '')
                    img_dt = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
                except Exception:
                    continue
                
                # Date filtering
                if start_date:
                    start_dt = datetime.strptime(f"{start_date} {start_time}", '%Y-%m-%d %H:%M')
                    if img_dt < start_dt:
                        continue
                        
                if end_date:
                    end_dt = datetime.strptime(f"{end_date} {end_time}", '%Y-%m-%d %H:%M')
                    if img_dt > end_dt:
                        continue
                
                # Daily time range filtering
                if apply_daily_time:
                    img_time = img_dt.strftime('%H:%M')
                    if not (daily_start <= img_time <= daily_end):
                        continue
                
                img_path = f'{folder}/{img_file}'
                images.append({
                    'path': img_path,
                    'url': f'/stills/{img_path}',
                    'timestamp': img_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'datetime': img_dt.isoformat(),
                    'camera_id': cid
                })
        
        # Sort by timestamp (most recent first)
        images.sort(key=lambda x: x['timestamp'], reverse=True)
        
        return jsonify({
            'ok': True,
            'images': images,
            'total': len(images)
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# Timelapse Gallery API
# ============================================================

@timelapse_bp.route('/api/timelapse/gallery')
def api_get_timelapse_gallery():
    """Get list of created timelapse videos/GIFs"""
    try:
        cam_id = request.args.get('cam_id', type=int)
        timelapses = []
        seen_names = set()
        
        timelapse_dirs = [TIMELAPSE_OUTPUT, ARCHIVE_TIMELAPSE_ROOT]
        
        for tl_dir in timelapse_dirs:
            if not os.path.isdir(tl_dir):
                continue
            for filename in os.listdir(tl_dir):
                if filename in seen_names:
                    continue
                if not (filename.endswith('.mp4') or filename.endswith('.gif')):
                    continue
                seen_names.add(filename)
                
                filepath = os.path.join(tl_dir, filename)
                stat = os.stat(filepath)
                
                file_cam_id = None
                if filename.startswith('camera_'):
                    try:
                        file_cam_id = int(filename.split('_')[1])
                    except Exception:
                        pass
                
                if cam_id is not None and file_cam_id is not None and file_cam_id != cam_id:
                    continue
                
                size_mb = stat.st_size / (1024 * 1024)
                created = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                
                timelapses.append({
                    'name': filename,
                    'path': f'/timelapse_output/{filename}',
                    'size': f'{size_mb:.1f} MB',
                    'created': created,
                    'frames': '?',
                    'fps': '?'
                })
        
        # Sort by creation time (most recent first)
        timelapses.sort(key=lambda x: x['created'], reverse=True)
        
        return jsonify({
            'ok': True,
            'timelapses': timelapses
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# Timelapse Creation API
# ============================================================

@timelapse_bp.route('/api/timelapse/create', methods=['POST'])
def api_create_timelapse():
    """Create a timelapse video or GIF from images"""
    try:
        data = request.get_json()
        images = data.get('images', [])
        format_type = data.get('format', 'mp4')
        fps = data.get('fps', 15)
        add_progress_meter = data.get('add_progress_meter', False)
        add_clock_face = data.get('add_clock_face', False)
        
        if len(images) < 2:
            return jsonify({'ok': False, 'error': 'Need at least 2 images'})
        
        # Create output directory
        os.makedirs(TIMELAPSE_OUTPUT, exist_ok=True)
        
        # Generate output filename
        timestamp = int(time.time())
        output_filename = f'timelapse_{timestamp}.{format_type}'
        output_path = os.path.join(TIMELAPSE_OUTPUT, output_filename)
        
        if format_type == 'gif':
            # Create GIF using PIL
            from PIL import Image
            
            frames = []
            for img_path in images:
                full_path = _resolve_still(img_path)
                if full_path:
                    img = Image.open(full_path)
                    # Resize for GIF (max 800px width)
                    if img.width > 800:
                        ratio = 800 / img.width
                        img = img.resize((800, int(img.height * ratio)), Image.LANCZOS)
                    # Convert to RGB (GIFs don't support RGBA)
                    if img.mode == 'RGBA':
                        img = img.convert('RGB')
                    frames.append(img)
            
            if frames:
                duration = int(1000 / fps)
                frames[0].save(
                    output_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=duration,
                    loop=0
                )
        else:
            # Create MP4 using OpenCV
            import cv2
            
            # Get frame size from first image
            first_img = _resolve_still(images[0])
            if not first_img:
                return jsonify({'ok': False, 'error': 'Could not find first image'})
            frame = cv2.imread(first_img)
            if frame is None:
                return jsonify({'ok': False, 'error': 'Could not read first image'})
            
            height, width = frame.shape[:2]
            
            # Create video writer
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
            
            for img_path in images:
                full_path = _resolve_still(img_path)
                if not full_path:
                    continue
                frame = cv2.imread(full_path)
                if frame is not None:
                    # Resize if needed
                    if frame.shape[:2] != (height, width):
                        frame = cv2.resize(frame, (width, height))
                    out.write(frame)
            
            out.release()
        
        return jsonify({
            'ok': True,
            'filename': output_filename,
            'download_url': f'/timelapse_output/{output_filename}'
        })
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# Delete Images API
# ============================================================

@timelapse_bp.route('/api/timelapse/delete', methods=['POST'])
def api_delete_timelapse_images():
    """Delete timelapse images"""
    try:
        data = request.get_json()
        images = data.get('images', [])
        deleted = 0
        
        for img_path in images:
            full_path = _resolve_still(img_path)
            if full_path:
                os.remove(full_path)
                deleted += 1
        
        return jsonify({'ok': True, 'deleted': deleted})
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ============================================================
# Schedule Management
# ============================================================

def load_schedules():
    """Load schedules from file"""
    if os.path.exists(SCHEDULES_FILE):
        try:
            with open(SCHEDULES_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_schedules(schedules):
    """Save schedules to file"""
    with open(SCHEDULES_FILE, 'w') as f:
        json.dump(schedules, f, indent=2)


@timelapse_bp.route('/api/timelapse/schedule', methods=['GET', 'POST', 'DELETE'])
def api_timelapse_schedule():
    """Manage timelapse schedules"""
    if request.method == 'GET':
        # Return all schedules
        schedules = load_schedules()
        return jsonify({'ok': True, 'schedules': schedules})
    
    elif request.method == 'POST':
        # Create new schedule
        try:
            data = request.get_json()
            schedules = load_schedules()
            
            # Generate new ID
            new_id = max([s.get('id', 0) for s in schedules], default=0) + 1
            
            new_schedule = {
                'id': new_id,
                'camera_ids': data.get('camera_ids', []),
                'time': data.get('time', '00:00'),
                'window_hours': data.get('window_hours', 24),
                'frame_skip': data.get('frame_skip', 1),
                'fps': data.get('fps', 15),
                'format': data.get('format', 'mp4'),
                'add_progress_meter': data.get('add_progress_meter', False),
                'add_clock_face': data.get('add_clock_face', False),
                'name': data.get('name', f'Schedule {new_id}'),
                'interval_hours': data.get('interval_hours', 24),
                'enabled': True,
                'created_at': datetime.now().isoformat(),
                'last_run': None
            }
            
            schedules.append(new_schedule)
            save_schedules(schedules)
            
            return jsonify({'ok': True, 'schedule': new_schedule, 'count': 1})
        
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500
    
    elif request.method == 'DELETE':
        # Delete schedule by ID
        try:
            schedule_id = request.args.get('id', type=int)
            if schedule_id is None:
                return jsonify({'ok': False, 'error': 'Missing schedule ID'}), 400
            
            schedules = load_schedules()
            schedules = [s for s in schedules if s.get('id') != schedule_id]
            save_schedules(schedules)
            
            return jsonify({'ok': True})
        
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500
