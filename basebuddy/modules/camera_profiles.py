"""
Camera Profile Management - Per-camera configuration profiles
"""
import sqlite3
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
import json
import logging

logger = logging.getLogger(__name__)

from .config import DB_PATH


@dataclass
class CameraProfile:
    """Camera profile configuration"""
    camera_id: int
    name: Optional[str] = None
    purpose: Optional[str] = None
    template_name: Optional[str] = None  # Profile template this was created from
    camera_enabled: bool = True  # Controls frame grabbing
    detection_enabled: bool = True  # Controls detection (only if camera_enabled is True)
    detection_fps: float = 1.0
    detection_interval_frames: int = 30
    detection_classes: Optional[list] = None
    model_size: str = "nano"
    confidence_threshold: float = 0.5
    use_gpu: bool = True
    recording_enabled: bool = True
    recording_trigger_classes: Optional[list] = None
    recording_quality: str = "medium"
    recording_fps: float = 10.0
    face_recognition_enabled: bool = False
    pose_detection_enabled: bool = False
    motion_detection_enabled: bool = False
    # Timelapse/still capture settings
    still_capture_enabled: bool = False
    still_capture_interval_seconds: int = 60
    still_capture_folder: Optional[str] = None  # Custom folder for stills
    still_capture_start_hour: int = 6  # Start capturing at 6 AM
    still_capture_end_hour: int = 20  # Stop capturing at 8 PM (20:00)
    still_capture_skip_dark: bool = True  # Skip frames that are mostly black
    still_capture_min_brightness: int = 15  # Minimum mean brightness (0-255) to save frame
    # Image transformation
    rotation: int = 0  # 0, 90, 180, 270 degrees clockwise
    flip_horizontal: bool = False
    flip_vertical: bool = False
    # Audio
    audio_trigger_enabled: bool = False
    audio_threshold_db: float = -40.0
    # Resource management
    max_gpu_memory_mb: Optional[float] = None
    priority: int = 5


# ============ Profile Templates ============
# Predefined templates for common camera use cases

PROFILE_TEMPLATES = {
    "default": {
        "name": "Default",
        "description": "Standard security camera with AI detection",
        "detection_enabled": True,
        "recording_enabled": True,
        "still_capture_enabled": False,
    },
    "plantcam": {
        "name": "PlantCam (Timelapse)",
        "description": "Captures stills for timelapse videos (daylight hours only)",
        "detection_enabled": False,
        "recording_enabled": False,
        "still_capture_enabled": True,
        "still_capture_interval_seconds": 60,  # 1 minute default
        "still_capture_start_hour": 6,  # 6 AM
        "still_capture_end_hour": 20,  # 8 PM
    },
    "plantcam_fast": {
        "name": "PlantCam (Fast Timelapse)",
        "description": "Fast timelapse - 5 second intervals (daylight hours only)",
        "detection_enabled": False,
        "recording_enabled": False,
        "still_capture_enabled": True,
        "still_capture_interval_seconds": 5,
        "still_capture_start_hour": 6,
        "still_capture_end_hour": 20,
    },
    "security": {
        "name": "Security Camera",
        "description": "Full AI detection and recording",
        "detection_enabled": True,
        "detection_fps": 2.0,
        "recording_enabled": True,
        "still_capture_enabled": False,
    },
    "monitor_only": {
        "name": "Monitor Only",
        "description": "Live view only, no AI or recording",
        "detection_enabled": False,
        "recording_enabled": False,
        "still_capture_enabled": False,
    },
    "low_resource": {
        "name": "Low Resource",
        "description": "Minimal CPU/GPU usage",
        "detection_enabled": True,
        "detection_fps": 0.5,
        "recording_enabled": False,
        "still_capture_enabled": False,
        "use_gpu": False,
    },
}


def get_template(template_name: str) -> Optional[Dict[str, Any]]:
    """Get a profile template by name"""
    return PROFILE_TEMPLATES.get(template_name)


def apply_template(profile: CameraProfile, template_name: str) -> CameraProfile:
    """Apply a template to a profile, keeping camera_id and name"""
    template = PROFILE_TEMPLATES.get(template_name)
    if not template:
        return profile
    
    # Keep identity fields
    camera_id = profile.camera_id
    name = profile.name
    
    # Apply template values
    for key, value in template.items():
        if key not in ('name', 'description') and hasattr(profile, key):
            setattr(profile, key, value)
    
    profile.camera_id = camera_id
    profile.name = name
    profile.template_name = template_name
    
    return profile


class CameraProfileManager:
    """Manages camera profiles"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._schema_checked = False
    
    def _connect(self):
        """Open database connection"""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_schema(self, cursor):
        """Ensure database has all required columns"""
        if self._schema_checked:
            return
        
        # List of columns to add if missing
        new_columns = [
            ('camera_enabled', 'INTEGER DEFAULT 1'),
            ('template_name', 'TEXT'),
            ('rotation', 'INTEGER DEFAULT 0'),
            ('flip_horizontal', 'INTEGER DEFAULT 0'),
            ('flip_vertical', 'INTEGER DEFAULT 0'),
            ('still_capture_folder', 'TEXT'),
            ('still_capture_start_hour', 'INTEGER DEFAULT 6'),
            ('still_capture_end_hour', 'INTEGER DEFAULT 20'),
            ('still_capture_skip_dark', 'INTEGER DEFAULT 1'),
            ('still_capture_min_brightness', 'INTEGER DEFAULT 15'),
        ]
        
        for col_name, col_def in new_columns:
            try:
                cursor.execute(f'ALTER TABLE camera_profiles ADD COLUMN {col_name} {col_def}')
                cursor.connection.commit()
                logger.info(f"ℹ  camera_profiles table upgraded with {col_name} column")
            except sqlite3.OperationalError:
                pass  # Column already exists
        
        self._schema_checked = True
    
    def get_profile(self, camera_id: int) -> Optional[CameraProfile]:
        """Get profile for a camera, or return default"""
        with self._connect() as conn:
            cursor = conn.cursor()
            self._ensure_schema(cursor)
            
            cursor.execute('SELECT * FROM camera_profiles WHERE camera_id = ?', (camera_id,))
            row = cursor.fetchone()
            
            if row:
                return self._row_to_profile(row)
            else:
                # Return default profile
                return CameraProfile(camera_id=camera_id)
    
    def save_profile(self, profile: CameraProfile):
        """Save or update a camera profile"""
        with self._connect() as conn:
            cursor = conn.cursor()
            self._ensure_schema(cursor)
            
            # Convert lists to JSON strings
            detection_classes_json = json.dumps(profile.detection_classes) if profile.detection_classes else None
            recording_trigger_classes_json = json.dumps(profile.recording_trigger_classes) if profile.recording_trigger_classes else None
            
            cursor.execute('''
                INSERT OR REPLACE INTO camera_profiles (
                    camera_id, name, purpose, template_name, camera_enabled, detection_enabled, detection_fps,
                    detection_interval_frames, detection_classes, model_size,
                    confidence_threshold, use_gpu, recording_enabled,
                    recording_trigger_classes, recording_quality, recording_fps,
                    face_recognition_enabled, pose_detection_enabled,
                    motion_detection_enabled, still_capture_enabled,
                    still_capture_interval_seconds, still_capture_folder,
                    still_capture_start_hour, still_capture_end_hour,
                    still_capture_skip_dark, still_capture_min_brightness,
                    rotation, flip_horizontal, flip_vertical,
                    audio_trigger_enabled, audio_threshold_db, max_gpu_memory_mb, priority,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                profile.camera_id,
                profile.name,
                profile.purpose,
                profile.template_name,
                1 if profile.camera_enabled else 0,
                1 if profile.detection_enabled else 0,
                profile.detection_fps,
                profile.detection_interval_frames,
                detection_classes_json,
                profile.model_size,
                profile.confidence_threshold,
                1 if profile.use_gpu else 0,
                1 if profile.recording_enabled else 0,
                recording_trigger_classes_json,
                profile.recording_quality,
                profile.recording_fps,
                1 if profile.face_recognition_enabled else 0,
                1 if profile.pose_detection_enabled else 0,
                1 if profile.motion_detection_enabled else 0,
                1 if profile.still_capture_enabled else 0,
                profile.still_capture_interval_seconds,
                profile.still_capture_folder,
                profile.still_capture_start_hour,
                profile.still_capture_end_hour,
                1 if profile.still_capture_skip_dark else 0,
                profile.still_capture_min_brightness,
                profile.rotation,
                1 if profile.flip_horizontal else 0,
                1 if profile.flip_vertical else 0,
                1 if profile.audio_trigger_enabled else 0,
                profile.audio_threshold_db,
                profile.max_gpu_memory_mb,
                profile.priority
            ))
            conn.commit()
    
    def get_all_profiles(self) -> Dict[int, CameraProfile]:
        """Get all camera profiles"""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM camera_profiles')
            rows = cursor.fetchall()
            
            profiles = {}
            for row in rows:
                profile = self._row_to_profile(row)
                profiles[profile.camera_id] = profile
            
            return profiles
    
    def _row_to_profile(self, row: sqlite3.Row) -> CameraProfile:
        """Convert database row to CameraProfile"""
        detection_classes = None
        if row['detection_classes']:
            try:
                detection_classes = json.loads(row['detection_classes'])
            except Exception:
                pass
        
        recording_trigger_classes = None
        if row['recording_trigger_classes']:
            try:
                recording_trigger_classes = json.loads(row['recording_trigger_classes'])
            except Exception:
                pass
        
        # Helper to safely get column value with default
        def get_col(name, default=None):
            try:
                val = row[name]
                return val if val is not None else default
            except (KeyError, IndexError):
                return default
        
        return CameraProfile(
            camera_id=row['camera_id'],
            name=row['name'],
            purpose=row['purpose'],
            template_name=get_col('template_name'),
            camera_enabled=bool(get_col('camera_enabled', 1)),
            detection_enabled=bool(row['detection_enabled']),
            detection_fps=row['detection_fps'],
            detection_interval_frames=row['detection_interval_frames'],
            detection_classes=detection_classes,
            model_size=row['model_size'],
            confidence_threshold=row['confidence_threshold'],
            use_gpu=bool(row['use_gpu']),
            recording_enabled=bool(row['recording_enabled']),
            recording_trigger_classes=recording_trigger_classes,
            recording_quality=row['recording_quality'],
            recording_fps=row['recording_fps'],
            face_recognition_enabled=bool(row['face_recognition_enabled']),
            pose_detection_enabled=bool(row['pose_detection_enabled']),
            motion_detection_enabled=bool(row['motion_detection_enabled']),
            still_capture_enabled=bool(row['still_capture_enabled']),
            still_capture_interval_seconds=row['still_capture_interval_seconds'],
            still_capture_folder=get_col('still_capture_folder'),
            still_capture_start_hour=get_col('still_capture_start_hour', 6),
            still_capture_end_hour=get_col('still_capture_end_hour', 20),
            still_capture_skip_dark=bool(get_col('still_capture_skip_dark', 1)),
            still_capture_min_brightness=get_col('still_capture_min_brightness', 15),
            rotation=get_col('rotation', 0),
            flip_horizontal=bool(get_col('flip_horizontal', 0)),
            flip_vertical=bool(get_col('flip_vertical', 0)),
            audio_trigger_enabled=bool(row['audio_trigger_enabled']),
            audio_threshold_db=row['audio_threshold_db'],
            max_gpu_memory_mb=row['max_gpu_memory_mb'],
            priority=row['priority']
        )
    
    def to_dict(self, profile: CameraProfile) -> Dict[str, Any]:
        """Convert profile to dictionary for JSON serialization"""
        return {
            'camera_id': profile.camera_id,
            'name': profile.name,
            'purpose': profile.purpose,
            'template_name': profile.template_name,
            'camera_enabled': profile.camera_enabled,
            'detection_enabled': profile.detection_enabled,
            'detection_fps': profile.detection_fps,
            'detection_interval_frames': profile.detection_interval_frames,
            'detection_classes': profile.detection_classes,
            'model_size': profile.model_size,
            'confidence_threshold': profile.confidence_threshold,
            'use_gpu': profile.use_gpu,
            'recording_enabled': profile.recording_enabled,
            'recording_trigger_classes': profile.recording_trigger_classes,
            'recording_quality': profile.recording_quality,
            'recording_fps': profile.recording_fps,
            'face_recognition_enabled': profile.face_recognition_enabled,
            'pose_detection_enabled': profile.pose_detection_enabled,
            'motion_detection_enabled': profile.motion_detection_enabled,
            'still_capture_enabled': profile.still_capture_enabled,
            'still_capture_interval_seconds': profile.still_capture_interval_seconds,
            'still_capture_folder': profile.still_capture_folder,
            'still_capture_start_hour': profile.still_capture_start_hour,
            'still_capture_end_hour': profile.still_capture_end_hour,
            'still_capture_skip_dark': profile.still_capture_skip_dark,
            'still_capture_min_brightness': profile.still_capture_min_brightness,
            'rotation': profile.rotation,
            'flip_horizontal': profile.flip_horizontal,
            'flip_vertical': profile.flip_vertical,
            'audio_trigger_enabled': profile.audio_trigger_enabled,
            'audio_threshold_db': profile.audio_threshold_db,
            'max_gpu_memory_mb': profile.max_gpu_memory_mb,
            'priority': profile.priority
        }


# Global singleton
_profile_manager: Optional[CameraProfileManager] = None


def get_profile_manager() -> CameraProfileManager:
    """Get or create global profile manager"""
    global _profile_manager
    if _profile_manager is None:
        _profile_manager = CameraProfileManager()
    return _profile_manager

