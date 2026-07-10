"""
Multiview Time Synchronization and Frame Management

Handles synchronized frame capture across multiple cameras,
time-synced timelapse image retrieval, and live feed management.
"""

import os
import glob
import cv2
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
import logging

from basebuddy.core.paths import get_stills_root, stills_search_roots

logger = logging.getLogger('basebuddy.multiview.sync')


def _list_still_jpgs(folder_name: str) -> Dict[str, str]:
    """Return {filename: absolute_path} for .jpg stills across search roots."""
    files: Dict[str, str] = {}
    for root in reversed(stills_search_roots()):
        folder = os.path.join(root, folder_name)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.endswith(".jpg"):
                files[name] = os.path.join(folder, name)
    return files


class MultiviewFrameSync:
    """
    Manages synchronized frame retrieval across multiple cameras.
    
    Supports:
    - Live capture from running cameras
    - Time-synced timelapse image retrieval
    - Nearest-neighbor time matching across cameras
    """
    
    def __init__(self, max_time_diff_seconds: float = 60.0):
        """
        Args:
            max_time_diff_seconds: Maximum allowed time difference for "synchronized" frames
        """
        self.max_time_diff = max_time_diff_seconds
    
    def get_cameras_with_timelapse(self) -> List[Dict]:
        """Get list of cameras that have timelapse images."""
        from basebuddy.modules.camera_profiles import get_profile_manager
        
        cameras_by_id: Dict[int, Dict] = {}
        profile_manager = get_profile_manager()
        
        seen_folders: set[str] = set()
        for root in stills_search_roots():
            if not os.path.isdir(root):
                continue
            for folder in sorted(os.listdir(root)):
                if not folder.startswith('camera_') or folder in seen_folders:
                    continue
                seen_folders.add(folder)
                
                try:
                    cam_id = int(folder.replace('camera_', ''))
                except ValueError:
                    continue
                
                jpgs = _list_still_jpgs(folder)
                if not jpgs:
                    continue
                
                filenames = sorted(jpgs.keys())
                latest_filename = filenames[-1]
                latest_image = jpgs[latest_filename]
                
                try:
                    dt_str = latest_filename.replace('.jpg', '')
                    latest_dt = datetime.strptime(dt_str, '%Y%m%d_%H%M%S')
                except ValueError:
                    latest_dt = None
                
                profile = profile_manager.get_profile(cam_id)
                name = profile.name if profile and profile.name else f"Camera {cam_id + 1}"
                
                existing = cameras_by_id.get(cam_id)
                if existing and existing['num_images'] >= len(jpgs):
                    continue
                
                cameras_by_id[cam_id] = {
                    'id': cam_id,
                    'name': name,
                    'folder': os.path.dirname(latest_image),
                    'num_images': len(jpgs),
                    'latest_image': latest_image,
                    'latest_timestamp': latest_dt.isoformat() if latest_dt else None,
                    'thumbnail_url': f'/stills/{folder}/{latest_filename}',
                }
        
        return sorted(cameras_by_id.values(), key=lambda c: c['id'])
    
    def get_timelapse_images(self, camera_id: int, 
                            start_time: Optional[datetime] = None,
                            end_time: Optional[datetime] = None,
                            limit: int = 100) -> List[Dict]:
        """Get timelapse images for a camera within time range."""
        folder = f'camera_{camera_id}'
        jpgs = _list_still_jpgs(folder)
        if not jpgs:
            return []
        
        images = []
        for filename in sorted(jpgs.keys()):
            try:
                dt_str = filename.replace('.jpg', '')
                dt = datetime.strptime(dt_str, '%Y%m%d_%H%M%S')
                
                if start_time and dt < start_time:
                    continue
                if end_time and dt > end_time:
                    continue
                
                images.append({
                    'filename': filename,
                    'path': jpgs[filename],
                    'timestamp': dt,
                    'timestamp_str': dt.isoformat()
                })
            except ValueError:
                continue
        
        images.sort(key=lambda x: x['timestamp'], reverse=True)
        return images[:limit]
    
    def find_synced_frames(self, camera_ids: List[int], 
                          target_time: Optional[datetime] = None,
                          use_latest: bool = True) -> Dict[int, Dict]:
        """
        Find time-synchronized frames across multiple cameras.
        
        Args:
            camera_ids: List of camera IDs to sync
            target_time: Target timestamp to match (optional)
            use_latest: If True and no target_time, use most recent frame
            
        Returns:
            Dict mapping camera_id -> {path, timestamp, time_diff}
        """
        if not camera_ids:
            return {}
        
        if target_time is None and use_latest:
            all_latest = []
            for cam_id in camera_ids:
                images = self.get_timelapse_images(cam_id, limit=1)
                if images:
                    all_latest.append((cam_id, images[0]))
            
            if not all_latest:
                return {}
            
            all_latest.sort(key=lambda x: x[1]['timestamp'], reverse=True)
            target_time = all_latest[0][1]['timestamp']
        
        if target_time is None:
            return {}
        
        synced_frames = {}
        
        for cam_id in camera_ids:
            window_start = target_time - timedelta(seconds=self.max_time_diff)
            window_end = target_time + timedelta(seconds=self.max_time_diff)
            
            images = self.get_timelapse_images(cam_id, window_start, window_end, limit=20)
            
            if not images:
                logger.warning(f"No images found for camera {cam_id} near {target_time}")
                continue
            
            best_match = None
            best_diff = float('inf')
            
            for img in images:
                diff = abs((img['timestamp'] - target_time).total_seconds())
                if diff < best_diff:
                    best_diff = diff
                    best_match = img
            
            if best_match and best_diff <= self.max_time_diff:
                synced_frames[cam_id] = {
                    'path': best_match['path'],
                    'timestamp': best_match['timestamp'],
                    'time_diff_seconds': best_diff
                }
        
        return synced_frames
    
    def capture_live_synced(self, camera_ids: List[int]) -> Dict[int, Dict]:
        """
        Capture live frames from cameras (as synchronized as possible).
        
        Returns:
            Dict mapping camera_id -> {frame, path, timestamp}
        """
        from basebuddy.modules.state import grabbers
        
        captured = {}
        capture_time = datetime.now()
        
        for cam_id in camera_ids:
            grabber = grabbers.get(cam_id)
            if grabber is None:
                logger.warning(f"No grabber for camera {cam_id}")
                continue
            
            try:
                frame, ts = grabber.get_latest_frame()
                if frame is not None:
                    captured[cam_id] = {
                        'frame': frame,
                        'timestamp': datetime.fromtimestamp(ts) if ts else capture_time,
                        'capture_time': capture_time
                    }
            except Exception as e:
                logger.error(f"Error capturing from camera {cam_id}: {e}")
        
        return captured
    
    def load_frames_from_paths(self, frame_info: Dict[int, Dict]) -> Dict[int, np.ndarray]:
        """
        Load frames from file paths.
        
        Args:
            frame_info: Dict from find_synced_frames()
            
        Returns:
            Dict mapping camera_id -> frame (numpy array)
        """
        frames = {}
        
        for cam_id, info in frame_info.items():
            path = info.get('path')
            if path and os.path.exists(path):
                frame = cv2.imread(path)
                if frame is not None:
                    frames[cam_id] = frame
        
        return frames


class MultiviewSession:
    """
    Manages a multiview 3D reconstruction session.
    
    Tracks selected cameras, calibration state, and reconstruction progress.
    Provides real-time feedback for matched points visualization.
    """
    
    def __init__(self, session_id: str = None):
        from basebuddy.core.paths import get_repo_root
        
        self.session_id = session_id or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.selected_cameras: List[int] = []
        self.calibration_state = {}
        self.matched_points_history = []
        self.reconstruction_quality = 0.0
        
        self.session_dir = os.path.join(
            get_repo_root(),
            'multiview_data', 'sessions', self.session_id
        )
        Path(self.session_dir).mkdir(parents=True, exist_ok=True)
    
    def select_cameras(self, camera_ids: List[int]):
        """Set the cameras for this session"""
        self.selected_cameras = camera_ids
        logger.info(f"Session {self.session_id}: Selected cameras {camera_ids}")
    
    def add_matched_points(self, cam1_id: int, cam2_id: int, 
                          points1: np.ndarray, points2: np.ndarray,
                          inlier_mask: np.ndarray = None):
        """Record matched points between two cameras"""
        self.matched_points_history.append({
            'timestamp': datetime.now().isoformat(),
            'cam1_id': cam1_id,
            'cam2_id': cam2_id,
            'num_points': len(points1),
            'num_inliers': int(np.sum(inlier_mask)) if inlier_mask is not None else len(points1)
        })
    
    def get_calibration_progress(self) -> Dict:
        """Get current calibration progress"""
        return {
            'session_id': self.session_id,
            'cameras': self.selected_cameras,
            'calibration_state': self.calibration_state,
            'total_matched_points': sum(h['num_points'] for h in self.matched_points_history),
            'total_inliers': sum(h['num_inliers'] for h in self.matched_points_history),
            'quality': self.reconstruction_quality
        }
    
    def save_state(self):
        """Save session state to disk"""
        import json
        state_file = os.path.join(self.session_dir, 'session_state.json')
        state = {
            'session_id': self.session_id,
            'selected_cameras': self.selected_cameras,
            'calibration_state': self.calibration_state,
            'matched_points_history': self.matched_points_history,
            'quality': self.reconstruction_quality
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    
    def load_state(self):
        """Load session state from disk"""
        import json
        state_file = os.path.join(self.session_dir, 'session_state.json')
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                state = json.load(f)
                self.selected_cameras = state.get('selected_cameras', [])
                self.calibration_state = state.get('calibration_state', {})
                self.matched_points_history = state.get('matched_points_history', [])
                self.reconstruction_quality = state.get('quality', 0.0)
