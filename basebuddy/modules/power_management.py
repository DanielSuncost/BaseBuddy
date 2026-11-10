"""Detection rate scaling by time of day and recent detection quality."""
import time
from datetime import datetime, time as dt_time
from typing import Dict, Optional, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class PowerProfile(Enum):
    """Power consumption profiles"""
    MAXIMUM = "maximum"      # Full power, all features enabled
    HIGH = "high"            # High power, most features enabled
    MEDIUM = "medium"        # Medium power, reduced processing
    LOW = "low"              # Low power, minimal processing
    MINIMUM = "minimum"      # Minimum power, essential only


class PowerManager:
    """Manages power consumption by adjusting processing parameters"""
    
    def __init__(self):
        self.current_profile = None  # None = auto (time-based), otherwise manual override
        self.night_start = dt_time(22, 0)  # 10 PM
        self.night_end = dt_time(6, 0)     # 6 AM
        self.transition_duration_minutes = 30  # Gradual transition period
        
        # Power profiles configuration
        self.profiles = {
            PowerProfile.MAXIMUM: {
                'ai_fps': 10,              # Detection frequency (frames per second)
                'face_recognition_interval': 1.0,  # Seconds between face recognition
                'pose_detection_interval': 1.0,    # Seconds between pose detection
                'frame_skip_rate': 1,       # Process every frame
                'recording_quality': 'high',  # Recording quality preset
                'gpu_enabled': True,       # Use GPU acceleration
                'enable_face_recognition': True,
                'enable_pose_detection': True,
                'enable_tracking': True,
                'model_size': 'large',     # Use larger models for accuracy
            },
            PowerProfile.HIGH: {
                'ai_fps': 6,
                'face_recognition_interval': 1.5,
                'pose_detection_interval': 1.5,
                'frame_skip_rate': 1,
                'recording_quality': 'high',
                'gpu_enabled': True,
                'enable_face_recognition': True,
                'enable_pose_detection': True,
                'enable_tracking': True,
                'model_size': 'medium',
            },
            PowerProfile.MEDIUM: {
                'ai_fps': 3,
                'face_recognition_interval': 3.0,
                'pose_detection_interval': 3.0,
                'frame_skip_rate': 2,       # Process every 2nd frame
                'recording_quality': 'medium',
                'gpu_enabled': True,
                'enable_face_recognition': True,
                'enable_pose_detection': False,  # Disable pose at night
                'enable_tracking': True,
                'model_size': 'small',
            },
            PowerProfile.LOW: {
                'ai_fps': 1,
                'face_recognition_interval': 5.0,
                'pose_detection_interval': 0,    # Disabled
                'frame_skip_rate': 5,       # Process every 5th frame
                'recording_quality': 'low',
                'gpu_enabled': False,       # Use CPU instead
                'enable_face_recognition': False,
                'enable_pose_detection': False,
                'enable_tracking': True,
                'model_size': 'small',
            },
            PowerProfile.MINIMUM: {
                'ai_fps': 0.5,              # 1 detection every 2 seconds
                'face_recognition_interval': 0,  # Disabled
                'pose_detection_interval': 0,
                'frame_skip_rate': 10,      # Process every 10th frame
                'recording_quality': 'low',
                'gpu_enabled': False,
                'enable_face_recognition': False,
                'enable_pose_detection': False,
                'enable_tracking': False,
                'model_size': 'tiny',
            }
        }
        
        # Current state
        self.last_update = time.time()
        # Per-camera timestamps of the last allowed detection, used to rate
        # limit detection to the profile's ai_fps for each camera.
        self._last_detection_times: Dict = {}
        # Per-camera AI FPS overrides from camera profiles (None = use power profile).
        self._camera_ai_fps: Dict = {}

    def set_camera_ai_fps(self, camera_id, fps) -> None:
        """Set or clear a per-camera detection FPS override from the camera profile."""
        if camera_id is None:
            return
        if fps is None:
            self._camera_ai_fps.pop(camera_id, None)
            return
        try:
            value = float(fps)
        except (TypeError, ValueError):
            return
        if value <= 0:
            self._camera_ai_fps.pop(camera_id, None)
            return
        try:
            from basebuddy.modules.config import DETECTION_FPS_MIN, DETECTION_FPS_MAX
            lo, hi = float(DETECTION_FPS_MIN), float(DETECTION_FPS_MAX)
        except Exception:
            lo, hi = 0.5, 30.0
        self._camera_ai_fps[camera_id] = max(lo, min(value, hi))

    def is_night_time(self) -> bool:
        """Check if current time is within night hours"""
        now = datetime.now().time()
        
        # Handle overnight period (e.g., 10 PM to 6 AM)
        if self.night_start > self.night_end:
            # Overnight period
            return now >= self.night_start or now <= self.night_end
        else:
            # Same-day period
            return self.night_start <= now <= self.night_end
    
    def get_time_based_profile(self) -> PowerProfile:
        """Determine power profile based on time of day"""
        if self.is_night_time():
            # Gradually transition to lower power during night
            now = datetime.now()
            night_start_dt = datetime.combine(now.date(), self.night_start)
            night_end_dt = datetime.combine(now.date(), self.night_end)
            
            # Adjust for overnight periods
            if self.night_start > self.night_end:
                if now.time() >= self.night_start:
                    night_start_dt = datetime.combine(now.date(), self.night_start)
                else:
                    night_start_dt = datetime.combine((now.date().replace(day=now.day-1) if now.day > 1 else now.date().replace(month=now.month-1, day=28)), self.night_start)
            
            minutes_into_night = (now - night_start_dt).total_seconds() / 60
            
            # Gradual transition: start at MEDIUM, move to LOW, then MINIMUM
            if minutes_into_night < self.transition_duration_minutes:
                # Transitioning into night - use MEDIUM
                return PowerProfile.MEDIUM
            elif minutes_into_night < self.transition_duration_minutes * 2:
                # Early night - use LOW
                return PowerProfile.LOW
            else:
                # Deep night - use MINIMUM
                return PowerProfile.MINIMUM
        else:
            # Daytime - use HIGH or MAXIMUM based on activity
            return PowerProfile.HIGH
    
    def get_current_settings(self) -> Dict:
        """Get current power management settings"""
        # Check for manual override first
        if self.current_profile is not None:
            profile = self.current_profile
        else:
            profile = self.get_time_based_profile()
        return self.profiles[profile].copy()
    
    def get_active_profile(self) -> PowerProfile:
        """Get the currently active profile (manual override or time-based)"""
        if self.current_profile is not None:
            return self.current_profile
        return self.get_time_based_profile()
    
    def should_process_frame(self, frame_count: int) -> bool:
        """Determine if a frame should be processed based on current power profile"""
        settings = self.get_current_settings()
        frame_skip_rate = settings.get('frame_skip_rate', 1)
        return frame_count % frame_skip_rate == 0
    
    def should_run_detection(self, camera_id=None) -> bool:
        """Rate-limit detection to ai_fps, per camera.

        Uses the camera profile override when set (detection_fps), otherwise
        the active power profile's ai_fps. Per-camera overrides let a camera
        request a higher sampling rate for tracking without changing global power.
        """
        settings = self.get_current_settings()
        ai_fps = float(settings.get('ai_fps', 3))
        if camera_id is not None and camera_id in self._camera_ai_fps:
            ai_fps = float(self._camera_ai_fps[camera_id])

        if ai_fps <= 0:
            return False

        now = time.time()
        min_interval = 1.0 / ai_fps
        last = self._last_detection_times.get(camera_id, 0.0)
        if now - last >= min_interval:
            self._last_detection_times[camera_id] = now
            return True
        return False
    
    def should_run_face_recognition(self, last_run_time: float) -> bool:
        """Check if face recognition should run"""
        settings = self.get_current_settings()
        if not settings.get('enable_face_recognition', True):
            return False
        
        interval = settings.get('face_recognition_interval', 2.0)
        return (time.time() - last_run_time) >= interval
    
    def should_run_pose_detection(self, last_run_time: float) -> bool:
        """Check if pose detection should run"""
        settings = self.get_current_settings()
        if not settings.get('enable_pose_detection', True):
            return False
        
        interval = settings.get('pose_detection_interval', 2.0)
        return (time.time() - last_run_time) >= interval
    
    def get_recording_quality(self) -> Tuple[str, str]:
        """Get recording quality settings (preset, crf)"""
        settings = self.get_current_settings()
        quality = settings.get('recording_quality', 'medium')
        
        quality_map = {
            'high': ('medium', '23'),      # Higher quality, more CPU
            'medium': ('fast', '28'),      # Balanced
            'low': ('ultrafast', '32'),    # Lower quality, less CPU
        }
        
        return quality_map.get(quality, ('fast', '28'))
    
    def get_model_size(self) -> str:
        """Get recommended model size for current power profile"""
        settings = self.get_current_settings()
        return settings.get('model_size', 'medium')
    
    def is_gpu_enabled(self) -> bool:
        """Check if GPU should be enabled"""
        settings = self.get_current_settings()
        return settings.get('gpu_enabled', True)
    
    def update_profile(self, profile: PowerProfile):
        """Manually set power profile (overrides time-based)"""
        self.current_profile = profile
        logger.info(f"Power profile set to: {profile.value.upper()}")
    
    def get_status(self) -> Dict:
        """Get current power management status"""
        profile = self.get_active_profile()
        settings = self.get_current_settings()
        
        return {
            'current_profile': profile.value,
            'is_night_time': self.is_night_time(),
            'settings': settings,
            'detection_rate': f"{settings['ai_fps']} fps",
            'frame_skip_rate': settings['frame_skip_rate'],
            'gpu_enabled': settings['gpu_enabled'],
        }


# Global instance
_power_manager: Optional[PowerManager] = None

def get_power_manager() -> PowerManager:
    """Get or create global power manager instance"""
    global _power_manager
    if _power_manager is None:
        _power_manager = PowerManager()
    return _power_manager

