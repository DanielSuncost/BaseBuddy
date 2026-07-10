"""
Resource Monitor - Tracks GPU, CPU, and RAM usage in real-time
"""
import threading
import time
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def _nvml_string(value) -> str:
    """NVML may return bytes (older bindings) or str (nvidia-ml-py >= 12)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


@dataclass
class GPUStats:
    """GPU resource statistics"""
    device_id: int
    name: str
    memory_total_mb: float
    memory_used_mb: float
    memory_free_mb: float
    memory_utilization_percent: float
    gpu_utilization_percent: float
    temperature_celsius: Optional[float] = None
    power_usage_watts: Optional[float] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SystemStats:
    """System resource statistics"""
    cpu_percent: float
    ram_total_mb: float
    ram_used_mb: float
    ram_free_mb: float
    ram_percent: float
    timestamp: datetime = field(default_factory=datetime.now)


class ResourceMonitor:
    """Monitors GPU, CPU, and RAM usage"""
    
    def __init__(self, update_interval: float = 0.5):
        self.update_interval = update_interval  # Update more frequently (every 0.5s)
        self.gpu_stats: Dict[int, GPUStats] = {}
        self.system_stats: Optional[SystemStats] = None
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.nvml_available = False
        
        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self.nvml_available = True
                logger.info("NVML initialized for GPU monitoring")
            except Exception as e:
                logger.error(f"NVML initialization failed: {e}")
                self.nvml_available = False
    
    def start(self):
        """Start monitoring thread"""
        if self.running:
            return
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info("Resource monitor started")
    
    def stop(self):
        """Stop monitoring thread"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
    
    def _monitor_loop(self):
        """Background monitoring loop"""
        while self.running:
            try:
                self._update_gpu_stats()
                self._update_system_stats()
            except Exception as e:
                logger.error(f"Error in resource monitoring: {e}")
            time.sleep(self.update_interval)
    
    def _update_gpu_stats(self):
        """Update GPU statistics"""
        if self.nvml_available:
            try:
                device_count = pynvml.nvmlDeviceGetCount()
                for device_id in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(device_id)
                    
                    # Get memory info
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    memory_total_mb = mem_info.total / (1024**2)
                    memory_used_mb = mem_info.used / (1024**2)
                    memory_free_mb = mem_info.free / (1024**2)
                    memory_utilization = (mem_info.used / mem_info.total) * 100 if mem_info.total > 0 else 0
                    
                    # Get utilization
                    util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_utilization = util.gpu
                    
                    # Get temperature
                    try:
                        temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    except Exception:
                        temp = None
                    
                    # Get power usage
                    try:
                        power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # Convert mW to W
                    except Exception:
                        power = None
                    
                    # Get device name
                    name = _nvml_string(pynvml.nvmlDeviceGetName(handle))
                    
                    stats = GPUStats(
                        device_id=device_id,
                        name=name,
                        memory_total_mb=memory_total_mb,
                        memory_used_mb=memory_used_mb,
                        memory_free_mb=memory_free_mb,
                        memory_utilization_percent=memory_utilization,
                        gpu_utilization_percent=gpu_utilization,
                        temperature_celsius=temp,
                        power_usage_watts=power
                    )
                    
                    with self.lock:
                        self.gpu_stats[device_id] = stats
            except Exception as e:
                logger.error(f"Error updating GPU stats via NVML: {e}")
        
        # Fallback to PyTorch if NVML not available
        elif TORCH_AVAILABLE and torch.cuda.is_available():
            try:
                for device_id in range(torch.cuda.device_count()):
                    memory_total_mb = torch.cuda.get_device_properties(device_id).total_memory / (1024**2)
                    memory_reserved_mb = torch.cuda.memory_reserved(device_id) / (1024**2)
                    memory_allocated_mb = torch.cuda.memory_allocated(device_id) / (1024**2)
                    memory_free_mb = memory_total_mb - memory_reserved_mb
                    memory_utilization = (memory_reserved_mb / memory_total_mb) * 100 if memory_total_mb > 0 else 0
                    
                    stats = GPUStats(
                        device_id=device_id,
                        name=torch.cuda.get_device_name(device_id),
                        memory_total_mb=memory_total_mb,
                        memory_used_mb=memory_reserved_mb,
                        memory_free_mb=memory_free_mb,
                        memory_utilization_percent=memory_utilization,
                        gpu_utilization_percent=0.0  # Not available via PyTorch
                    )
                    
                    with self.lock:
                        self.gpu_stats[device_id] = stats
            except Exception as e:
                logger.error(f"Error updating GPU stats via PyTorch: {e}")
    
    def _update_system_stats(self):
        """Update system CPU/RAM statistics"""
        if not PSUTIL_AVAILABLE:
            return
        
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            ram = psutil.virtual_memory()
            
            self.system_stats = SystemStats(
                cpu_percent=cpu_percent,
                ram_total_mb=ram.total / (1024**2),
                ram_used_mb=ram.used / (1024**2),
                ram_free_mb=ram.available / (1024**2),
                ram_percent=ram.percent
            )
        except Exception as e:
            logger.error(f"Error updating system stats: {e}")
    
    def get_gpu_stats(self, device_id: int = 0) -> Optional[GPUStats]:
        """Get GPU statistics for a device"""
        with self.lock:
            return self.gpu_stats.get(device_id)
    
    def get_all_gpu_stats(self) -> Dict[int, GPUStats]:
        """Get all GPU statistics"""
        with self.lock:
            return self.gpu_stats.copy()
    
    def get_system_stats(self) -> Optional[SystemStats]:
        """Get system statistics"""
        return self.system_stats
    
    def get_memory_pressure(self) -> float:
        """Get overall memory pressure (0.0-1.0)"""
        gpu_stats = self.get_gpu_stats()
        sys_stats = self.get_system_stats()
        
        pressures = []
        if gpu_stats:
            pressures.append(gpu_stats.memory_utilization_percent / 100.0)
        if sys_stats:
            pressures.append(sys_stats.ram_percent / 100.0)
        
        return max(pressures) if pressures else 0.0
    
    def is_gpu_available(self, required_memory_mb: float = 0) -> bool:
        """Check if GPU has enough free memory"""
        gpu_stats = self.get_gpu_stats()
        if not gpu_stats:
            return True  # No GPU monitoring, assume available
        
        if required_memory_mb > 0:
            return gpu_stats.memory_free_mb >= required_memory_mb * 1.1  # 10% buffer
        
        # Check if utilization is below threshold
        return gpu_stats.memory_utilization_percent < 90.0


# Global singleton instance
_resource_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Get or create global resource monitor instance"""
    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor()
        _resource_monitor.start()
    return _resource_monitor

