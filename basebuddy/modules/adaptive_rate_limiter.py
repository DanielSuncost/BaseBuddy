"""
Adaptive Rate Limiter - Adjusts processing rates based on resource availability
"""
import time
from typing import Optional, Dict
from .resource_manager import ResourceManager, ResourcePriority


# Global singleton instance
_adaptive_limiter: Optional['AdaptiveRateLimiter'] = None


def get_adaptive_limiter() -> 'AdaptiveRateLimiter':
    """Get or create global adaptive rate limiter instance"""
    global _adaptive_limiter
    if _adaptive_limiter is None:
        from .resource_manager import get_resource_manager
        resource_manager = get_resource_manager()
        _adaptive_limiter = AdaptiveRateLimiter(resource_manager)
    return _adaptive_limiter


class AdaptiveRateLimiter:
    """Adaptively adjusts processing rates based on resource availability"""
    
    def __init__(self, resource_manager: ResourceManager):
        self.resource_manager = resource_manager
        self.base_detection_fps = 10.0
        self.min_detection_fps = 1.0
        self.max_detection_fps = 30.0
        self.face_recognition_interval = 2.0  # seconds between face recognition calls
        self.pose_detection_interval = 1.0
    
    def get_detection_fps(self) -> float:
        """Get current detection FPS based on resource availability"""
        memory_pressure = self.resource_manager.monitor.get_memory_pressure()
        
        # Reduce FPS as memory pressure increases
        if memory_pressure > 0.9:
            return self.min_detection_fps
        elif memory_pressure > 0.8:
            return self.base_detection_fps * 0.5
        elif memory_pressure > 0.7:
            return self.base_detection_fps * 0.75
        else:
            return self.base_detection_fps
    
    def should_run_face_recognition(self, last_run_time: float) -> bool:
        """Determine if face recognition should run"""
        current_time = time.time()
        elapsed = current_time - last_run_time
        
        # Check GPU availability - be more aggressive about skipping
        gpu_stats = self.resource_manager.monitor.get_gpu_stats()
        if gpu_stats:
            # Skip if GPU memory is above 75% (more conservative threshold)
            if gpu_stats.memory_utilization_percent > 75:
                return False
            # Skip if GPU is currently in use by another process
            if self.resource_manager.current_gpu_user and self.resource_manager.current_gpu_user != "face_recognition":
                return False
        
        # Adaptive interval based on memory pressure
        memory_pressure = self.resource_manager.monitor.get_memory_pressure()
        adaptive_interval = self.face_recognition_interval
        
        if memory_pressure > 0.75:  # Lowered threshold from 0.8
            adaptive_interval *= 3  # Triple interval under pressure
        elif memory_pressure > 0.65:  # Lowered threshold from 0.6
            adaptive_interval *= 2  # Double interval under moderate pressure
        elif memory_pressure > 0.5:
            adaptive_interval *= 1.5
        
        return elapsed >= adaptive_interval
    
    def should_run_pose_detection(self, last_run_time: float) -> bool:
        """Determine if pose detection should run"""
        current_time = time.time()
        elapsed = current_time - last_run_time
        
        # Check GPU availability
        gpu_stats = self.resource_manager.monitor.get_gpu_stats()
        if gpu_stats and gpu_stats.memory_utilization_percent > 80:
            return False
        
        memory_pressure = self.resource_manager.monitor.get_memory_pressure()
        adaptive_interval = self.pose_detection_interval
        
        if memory_pressure > 0.8:
            adaptive_interval *= 2
        
        return elapsed >= adaptive_interval
    
    def update_config(self, config: Dict):
        """Update rate limiter configuration"""
        self.base_detection_fps = config.get('base_detection_fps', 10.0)
        self.min_detection_fps = config.get('min_detection_fps', 1.0)
        self.max_detection_fps = config.get('max_detection_fps', 30.0)
        self.face_recognition_interval = config.get('face_recognition_interval', 2.0)
        self.pose_detection_interval = config.get('pose_detection_interval', 1.0)

