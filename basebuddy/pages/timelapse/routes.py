"""
Timelapse page routes.
"""
import os
from flask import render_template
from basebuddy.core.paths import get_repo_root
from basebuddy.pages.timelapse import timelapse_bp

PROJECT_ROOT = get_repo_root()
STILLS_ROOT = os.path.join(PROJECT_ROOT, "stills")

from basebuddy.modules.config import ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER
ARCHIVE_STILLS_ROOT = os.path.join(ARCHIVE_DRIVE_PATH, ARCHIVE_FOLDER, "stills")


def _merged_camera_stills() -> dict:
    """Return {camera_folder: set(jpg_filenames)} across local + archive."""
    result: dict[str, set] = {}
    for root in (STILLS_ROOT, ARCHIVE_STILLS_ROOT):
        if not os.path.isdir(root):
            continue
        for folder in os.listdir(root):
            if not folder.startswith("camera_"):
                continue
            folder_path = os.path.join(root, folder)
            if not os.path.isdir(folder_path):
                continue
            jpgs = {f for f in os.listdir(folder_path) if f.endswith(".jpg")}
            result.setdefault(folder, set()).update(jpgs)
    return result


@timelapse_bp.route('/timelapse')
@timelapse_bp.route('/timelapse/<int:cam_id>')
def timelapse_page(cam_id=None):
    """Timelapse gallery page"""
    from basebuddy.modules.camera_profiles import get_profile_manager
    profile_manager = get_profile_manager()
    
    cameras_with_stills = []
    for folder, jpg_set in _merged_camera_stills().items():
        try:
            cid = int(folder.replace('camera_', ''))
            image_count = len(jpg_set)
            if image_count > 0:
                profile = profile_manager.get_profile(cid)
                name = profile.name if profile and profile.name else f'Camera {cid + 1}'
                
                most_recent = sorted(jpg_set, reverse=True)[0]
                thumbnail_path = f'/stills/camera_{cid}/{most_recent}'
                
                cameras_with_stills.append({
                    'id': cid,
                    'count': image_count,
                    'name': name,
                    'thumbnail': thumbnail_path
                })
        except Exception:
            pass
    
    cameras_with_stills.sort(key=lambda x: x['id'])
    
    return render_template('timelapse.html',
                          active_page='timelapse',
                          cam_id=cam_id,
                          cameras=cameras_with_stills)
