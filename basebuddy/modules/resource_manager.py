"""
Resource Manager - Coordinates access to shared resources (GPU, CPU)
"""
import threading
import time
from typing import Optional, Dict
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

from .resource_monitor import ResourceMonitor


class ResourcePriority(Enum):
    """Priority levels for resource requests"""
    CRITICAL = 1  # Detection (must run)
    HIGH = 2      # Face recognition
    MEDIUM = 3    # Pose detection
    LOW = 4       # Background processing


@dataclass
class ResourceRequest:
    """Request for GPU resource access"""
    requester_id: str
    priority: ResourcePriority
    estimated_memory_mb: float
    timeout_seconds: float = 30.0
    timestamp: datetime = field(default_factory=datetime.now)


class ResourceManager:
    """Manages access to shared resources (GPU, CPU)"""
    
    def __init__(self, resource_monitor: ResourceMonitor, max_gpu_memory_mb: Optional[float] = None):
        self.monitor = resource_monitor
        self.max_gpu_memory_mb = max_gpu_memory_mb
        self.gpu_lock = threading.Lock()
        self.current_gpu_user: Optional[str] = None
        self.gpu_queue: list = []  # Queue of pending requests
        self.request_history: Dict[str, ResourceRequest] = {}
        self.lock = threading.Lock()
        
        # Configuration
        self.gpu_memory_threshold_percent = 0.85  # Don't allocate if >85% used
        self.enable_opportunistic_processing = True
        self.allow_critical_override = True  # Critical requests can override
    
    def request_gpu_access(
        self,
        requester_id: str,
        priority: ResourcePriority,
        estimated_memory_mb: float,
        timeout_seconds: float = 30.0,
        blocking: bool = True
    ) -> bool:
        """
        Request GPU access. Returns True if granted, False if denied.
        
        Args:
            requester_id: Unique identifier for the requester
            priority: Priority level (CRITICAL, HIGH, MEDIUM, LOW)
            estimated_memory_mb: Estimated GPU memory needed in MB
            timeout_seconds: Maximum time to wait for access
            blocking: If False, return immediately without waiting
        
        Returns:
            True if access granted, False if denied or timeout
        """
        request = ResourceRequest(
            requester_id=requester_id,
            priority=priority,
            estimated_memory_mb=estimated_memory_mb,
            timeout_seconds=timeout_seconds
        )
        
        with self.lock:
            self.request_history[requester_id] = request
        
        # Check if we can grant immediate access
        if self._can_grant_gpu_access(estimated_memory_mb, priority):
            with self.gpu_lock:
                if self.current_gpu_user is None:
                    self.current_gpu_user = requester_id
                    return True
                # Critical requests can override non-critical
                elif self.allow_critical_override and priority == ResourcePriority.CRITICAL:
                    current_priority = self._get_current_user_priority()
                    if current_priority and current_priority.value > priority.value:
                        # Preempt current user
                        self.current_gpu_user = requester_id
                        return True
        
        if not blocking:
            return False
        
        # If opportunistic processing is enabled and priority is low, skip
        if self.enable_opportunistic_processing and priority.value >= ResourcePriority.MEDIUM.value:
            gpu_stats = self.monitor.get_gpu_stats()
            if gpu_stats and gpu_stats.memory_utilization_percent > 80:
                return False  # Skip low-priority tasks when GPU is busy
        
        # Add to queue and wait
        with self.lock:
            if request not in self.gpu_queue:
                self.gpu_queue.append(request)
                self.gpu_queue.sort(key=lambda r: r.priority.value)
        
        # Wait for access (with timeout)
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if self._check_gpu_access_granted(requester_id, estimated_memory_mb, priority):
                return True
            time.sleep(0.1)
        
        # Timeout - remove from queue
        with self.lock:
            if request in self.gpu_queue:
                self.gpu_queue.remove(request)
        
        return False
    
    def release_gpu_access(self, requester_id: str):
        """Release GPU access"""
        with self.gpu_lock:
            if self.current_gpu_user == requester_id:
                self.current_gpu_user = None
        
        # Grant access to next in queue
        self._process_gpu_queue()
    
    def _can_grant_gpu_access(self, estimated_memory_mb: float, priority: ResourcePriority) -> bool:
        """Check if GPU access can be granted"""
        gpu_stats = self.monitor.get_gpu_stats()
        if not gpu_stats:
            return True  # No GPU monitoring, allow access
        
        # More aggressive memory checks - use a higher threshold for non-critical operations
        memory_threshold = self.gpu_memory_threshold_percent * 100
        
        # For non-critical operations, use a stricter threshold
        if priority.value > ResourcePriority.CRITICAL.value:
            # High priority: 75% threshold
            if priority == ResourcePriority.HIGH:
                memory_threshold = 75.0
            # Medium/Low priority: 70% threshold
            else:
                memory_threshold = 70.0
        
        # Check utilization threshold FIRST (before memory calculations)
        if gpu_stats.memory_utilization_percent > memory_threshold:
            # Only critical requests can override threshold
            if priority == ResourcePriority.CRITICAL and gpu_stats.memory_utilization_percent < 95:
                return True
            return False
        
        # Check memory availability
        memory_available_mb = gpu_stats.memory_free_mb
        if self.max_gpu_memory_mb:
            memory_available_mb = min(memory_available_mb, self.max_gpu_memory_mb - gpu_stats.memory_used_mb)
        
        # Check if we have enough memory (with larger buffer for safety)
        required_with_buffer = estimated_memory_mb * 1.2  # 20% buffer for safety
        if required_with_buffer > memory_available_mb:
            # Critical requests can proceed even if memory is tight (but still check)
            if priority == ResourcePriority.CRITICAL and gpu_stats.memory_utilization_percent < 90:
                return True
            return False
        
        return True
    
    def _check_gpu_access_granted(self, requester_id: str, estimated_memory_mb: float, priority: ResourcePriority) -> bool:
        """Check if access has been granted"""
        with self.gpu_lock:
            if self.current_gpu_user == requester_id:
                return True
        
        # Try to grant access if available
        if self._can_grant_gpu_access(estimated_memory_mb, priority):
            with self.lock:
                # Check if this request is next in queue
                if self.gpu_queue and self.gpu_queue[0].requester_id == requester_id:
                    with self.gpu_lock:
                        if self.current_gpu_user is None:
                            self.current_gpu_user = requester_id
                            self.gpu_queue.pop(0)
                            return True
        
        return False
    
    def _process_gpu_queue(self):
        """Process GPU access queue"""
        with self.lock:
            if not self.gpu_queue:
                return
            
            # Try to grant access to highest priority request
            for request in self.gpu_queue[:]:
                if self._can_grant_gpu_access(request.estimated_memory_mb, request.priority):
                    with self.gpu_lock:
                        if self.current_gpu_user is None:
                            self.current_gpu_user = request.requester_id
                            self.gpu_queue.remove(request)
                            break
    
    def _get_current_user_priority(self) -> Optional[ResourcePriority]:
        """Get priority of current GPU user"""
        if not self.current_gpu_user:
            return None
        
        with self.lock:
            request = self.request_history.get(self.current_gpu_user)
            return request.priority if request else None
    
    def get_resource_status(self) -> Dict:
        """Get current resource status"""
        gpu_stats = self.monitor.get_gpu_stats()
        sys_stats = self.monitor.get_system_stats()
        
        return {
            'gpu': {
                'current_user': self.current_gpu_user,
                'queue_length': len(self.gpu_queue),
                'queue': [
                    {
                        'requester_id': r.requester_id,
                        'priority': r.priority.name,
                        'estimated_memory_mb': r.estimated_memory_mb,
                        'waiting_seconds': (datetime.now() - r.timestamp).total_seconds()
                    }
                    for r in self.gpu_queue
                ],
                'stats': {
                    'memory_used_mb': gpu_stats.memory_used_mb if gpu_stats else 0,
                    'memory_total_mb': gpu_stats.memory_total_mb if gpu_stats else 0,
                    'memory_free_mb': gpu_stats.memory_free_mb if gpu_stats else 0,
                    'utilization_percent': gpu_stats.memory_utilization_percent if gpu_stats else 0,
                    'gpu_utilization_percent': gpu_stats.gpu_utilization_percent if gpu_stats else 0,
                    'temperature_celsius': gpu_stats.temperature_celsius if gpu_stats else None,
                    'power_usage_watts': gpu_stats.power_usage_watts if gpu_stats else None,
                    'name': gpu_stats.name if gpu_stats else 'Unknown'
                } if gpu_stats else None
            },
            'system': {
                'cpu_percent': sys_stats.cpu_percent if sys_stats else 0,
                'ram_percent': sys_stats.ram_percent if sys_stats else 0,
                'ram_used_mb': sys_stats.ram_used_mb if sys_stats else 0,
                'ram_total_mb': sys_stats.ram_total_mb if sys_stats else 0,
                'ram_free_mb': sys_stats.ram_free_mb if sys_stats else 0
            } if sys_stats else None,
            'memory_pressure': self.monitor.get_memory_pressure()
        }
    
    def update_config(self, config: Dict):
        """Update resource manager configuration"""
        self.gpu_memory_threshold_percent = config.get('gpu_memory_threshold_percent', 0.85)
        self.enable_opportunistic_processing = config.get('enable_opportunistic_processing', True)
        self.allow_critical_override = config.get('allow_critical_override', True)
        if 'max_gpu_memory_mb' in config:
            self.max_gpu_memory_mb = config['max_gpu_memory_mb']


# Global singleton instance
_resource_manager: Optional[ResourceManager] = None
_resource_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Get or create global resource monitor instance"""
    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor()
        _resource_monitor.start()
    return _resource_monitor


def get_resource_manager() -> ResourceManager:
    """Get or create global resource manager instance"""
    global _resource_manager
    if _resource_manager is None:
        monitor = get_resource_monitor()
        _resource_manager = ResourceManager(monitor)
    return _resource_manager

