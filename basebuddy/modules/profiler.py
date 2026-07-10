"""
Performance Profiler - Tracks per-camera performance metrics and identifies bottlenecks
"""
import time
import threading
import psutil
import os
from typing import Dict, Optional, List
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class CameraMetrics:
    """Performance metrics for a single camera"""
    camera_id: int
    
    # Frame Processing
    frames_processed: int = 0
    frames_dropped: int = 0
    frame_processing_times: deque = field(default_factory=lambda: deque(maxlen=100))
    avg_frame_processing_time_ms: float = 0.0
    
    # Detection
    detections_run: int = 0
    detection_times: deque = field(default_factory=lambda: deque(maxlen=100))
    avg_detection_time_ms: float = 0.0
    
    # Queue Metrics
    queue_depths: deque = field(default_factory=lambda: deque(maxlen=100))
    max_queue_depth: int = 0
    avg_queue_depth: float = 0.0
    
    # FPS
    fps_history: deque = field(default_factory=lambda: deque(maxlen=60))
    current_fps: float = 0.0
    target_fps: float = 15.0
    
    # Resource Usage
    gpu_memory_mb: float = 0.0
    cpu_time_percent: float = 0.0
    cpu_times: deque = field(default_factory=lambda: deque(maxlen=100))
    avg_cpu_percent: float = 0.0
    
    # Timestamps
    last_frame_time: Optional[float] = None
    last_detection_time: Optional[float] = None
    
    # Errors
    error_count: int = 0
    resource_exhausted_count: int = 0
    
    def update_frame_processing(self, processing_time_ms: float):
        """Update frame processing metrics"""
        self.frames_processed += 1
        self.frame_processing_times.append(processing_time_ms)
        if self.frame_processing_times:
            self.avg_frame_processing_time_ms = sum(self.frame_processing_times) / len(self.frame_processing_times)
        self.last_frame_time = time.time()
    
    def update_detection(self, detection_time_ms: float):
        """Update detection metrics"""
        self.detections_run += 1
        self.detection_times.append(detection_time_ms)
        if self.detection_times:
            self.avg_detection_time_ms = sum(self.detection_times) / len(self.detection_times)
        self.last_detection_time = time.time()
    
    def update_queue_depth(self, depth: int):
        """Update queue depth metrics"""
        self.queue_depths.append(depth)
        self.max_queue_depth = max(self.max_queue_depth, depth)
        if self.queue_depths:
            self.avg_queue_depth = sum(self.queue_depths) / len(self.queue_depths)
    
    def update_fps(self, fps: float):
        """Update FPS metrics"""
        self.fps_history.append(fps)
        self.current_fps = fps
    
    def record_error(self, is_resource_exhausted: bool = False):
        """Record an error"""
        self.error_count += 1
        if is_resource_exhausted:
            self.resource_exhausted_count += 1
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for API"""
        return {
            'camera_id': self.camera_id,
            'frames_processed': self.frames_processed,
            'frames_dropped': self.frames_dropped,
            'avg_frame_processing_time_ms': self.avg_frame_processing_time_ms,
            'detections_run': self.detections_run,
            'avg_detection_time_ms': self.avg_detection_time_ms,
            'max_queue_depth': self.max_queue_depth,
            'avg_queue_depth': self.avg_queue_depth,
            'current_fps': self.current_fps,
            'target_fps': self.target_fps,
            'gpu_memory_mb': self.gpu_memory_mb,
            'cpu_time_percent': self.cpu_time_percent,
            'avg_cpu_percent': self.avg_cpu_percent,
            'error_count': self.error_count,
            'resource_exhausted_count': self.resource_exhausted_count,
            'last_frame_time': self.last_frame_time,
            'last_detection_time': self.last_detection_time,
        }


class PerformanceProfiler:
    """Tracks performance metrics across all cameras"""
    
    def __init__(self):
        self.metrics: Dict[int, CameraMetrics] = {}
        self.lock = threading.Lock()
        self.start_time = time.time()
        
        # CPU profiling
        self.process = psutil.Process(os.getpid())
        self.cpu_monitoring = True
        self.cpu_monitor_thread = None
        self.system_cpu_percent = 0.0
        self.per_camera_cpu = {}  # Will track CPU usage per camera thread
        self._start_cpu_monitoring()
        
        # Bottleneck thresholds
        self.queue_depth_threshold = 10
        self.frame_processing_threshold_ms = 200.0
        self.detection_time_threshold_ms = 100.0
        self.frame_drop_rate_threshold = 0.1  # 10%
        self.cpu_threshold_percent = 80.0  # Alert if CPU > 80%
    
    def _start_cpu_monitoring(self):
        """Start background thread to monitor CPU usage"""
        if self.cpu_monitor_thread and self.cpu_monitor_thread.is_alive():
            return
        
        def monitor_cpu():
            while self.cpu_monitoring:
                try:
                    # System-wide CPU
                    self.system_cpu_percent = psutil.cpu_percent(interval=1.0)
                    
                    # Per-process CPU
                    process_cpu = self.process.cpu_percent(interval=0.1)
                    
                    # Update per-camera CPU (if we can identify threads)
                    # This is approximate - we track by camera_id in metrics
                    with self.lock:
                        for cam_id, metrics in self.metrics.items():
                            # Approximate CPU per camera (divide total by number of cameras)
                            num_cameras = max(1, len(self.metrics))
                            metrics.cpu_time_percent = process_cpu / num_cameras
                            metrics.cpu_times.append(metrics.cpu_time_percent)
                            if metrics.cpu_times:
                                metrics.avg_cpu_percent = sum(metrics.cpu_times) / len(metrics.cpu_times)
                    
                    time.sleep(2.0)  # Update every 2 seconds
                except Exception as e:
                    logger.error(f"CPU monitoring error: {e}")
                    time.sleep(5.0)
        
        self.cpu_monitor_thread = threading.Thread(target=monitor_cpu, daemon=True)
        self.cpu_monitor_thread.start()
    
    def get_or_create_metrics(self, camera_id: int) -> CameraMetrics:
        """Get or create metrics for a camera"""
        with self.lock:
            if camera_id not in self.metrics:
                self.metrics[camera_id] = CameraMetrics(camera_id=camera_id)
            return self.metrics[camera_id]
    
    def record_frame_processing(self, camera_id: int, processing_time_ms: float):
        """Record frame processing time"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.update_frame_processing(processing_time_ms)
    
    def record_detection(self, camera_id: int, detection_time_ms: float):
        """Record detection time"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.update_detection(detection_time_ms)
    
    def record_queue_depth(self, camera_id: int, depth: int):
        """Record queue depth"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.update_queue_depth(depth)
    
    def record_frame_dropped(self, camera_id: int):
        """Record a dropped frame"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.frames_dropped += 1
    
    def record_fps(self, camera_id: int, fps: float):
        """Record FPS"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.update_fps(fps)
    
    def record_error(self, camera_id: int, is_resource_exhausted: bool = False):
        """Record an error"""
        metrics = self.get_or_create_metrics(camera_id)
        metrics.record_error(is_resource_exhausted)
    
    def identify_bottlenecks(self) -> Dict[int, List[str]]:
        """Identify bottlenecks for each camera"""
        bottlenecks = defaultdict(list)
        
        with self.lock:
            for camera_id, metrics in self.metrics.items():
                # Check queue depth
                if metrics.avg_queue_depth > self.queue_depth_threshold:
                    bottlenecks[camera_id].append(f"High queue depth: {metrics.avg_queue_depth:.1f} (threshold: {self.queue_depth_threshold})")
                
                # Check frame processing time
                if metrics.avg_frame_processing_time_ms > self.frame_processing_threshold_ms:
                    bottlenecks[camera_id].append(f"Slow frame processing: {metrics.avg_frame_processing_time_ms:.1f}ms (threshold: {self.frame_processing_threshold_ms}ms)")
                
                # Check detection time
                if metrics.avg_detection_time_ms > self.detection_time_threshold_ms:
                    bottlenecks[camera_id].append(f"Slow detection: {metrics.avg_detection_time_ms:.1f}ms (threshold: {self.detection_time_threshold_ms}ms)")
                
                # Check frame drop rate
                total_frames = metrics.frames_processed + metrics.frames_dropped
                if total_frames > 0:
                    drop_rate = metrics.frames_dropped / total_frames
                    if drop_rate > self.frame_drop_rate_threshold:
                        bottlenecks[camera_id].append(f"High frame drop rate: {drop_rate*100:.1f}% (threshold: {self.frame_drop_rate_threshold*100}%)")
                
                # Check resource exhausted errors
                if metrics.resource_exhausted_count > 0:
                    bottlenecks[camera_id].append(f"Resource exhausted errors: {metrics.resource_exhausted_count}")
                
                # Check FPS
                if metrics.current_fps < metrics.target_fps * 0.8:
                    bottlenecks[camera_id].append(f"Low FPS: {metrics.current_fps:.1f} (target: {metrics.target_fps})")
                
                # Check CPU usage
                if metrics.avg_cpu_percent > self.cpu_threshold_percent:
                    bottlenecks[camera_id].append(f"High CPU usage: {metrics.avg_cpu_percent:.1f}% (threshold: {self.cpu_threshold_percent}%)")
        
        return dict(bottlenecks)
    
    def get_all_metrics(self) -> Dict[int, Dict]:
        """Get all camera metrics"""
        with self.lock:
            return {cam_id: metrics.to_dict() for cam_id, metrics in self.metrics.items()}
    
    def get_camera_metrics(self, camera_id: int) -> Optional[Dict]:
        """Get metrics for a specific camera"""
        with self.lock:
            if camera_id in self.metrics:
                return self.metrics[camera_id].to_dict()
            return None
    
    def get_summary(self) -> Dict:
        """Get summary statistics"""
        with self.lock:
            total_cameras = len(self.metrics)
            total_frames = sum(m.frames_processed for m in self.metrics.values())
            total_dropped = sum(m.frames_dropped for m in self.metrics.values())
            total_errors = sum(m.error_count for m in self.metrics.values())
            total_resource_errors = sum(m.resource_exhausted_count for m in self.metrics.values())
            
            avg_fps = sum(m.current_fps for m in self.metrics.values()) / total_cameras if total_cameras > 0 else 0
            avg_processing_time = sum(m.avg_frame_processing_time_ms for m in self.metrics.values()) / total_cameras if total_cameras > 0 else 0
            avg_detection_time = sum(m.avg_detection_time_ms for m in self.metrics.values()) / total_cameras if total_cameras > 0 else 0
            
            bottlenecks = self.identify_bottlenecks()
            
            total_cpu = sum(m.avg_cpu_percent for m in self.metrics.values())
            avg_cpu_per_camera = total_cpu / total_cameras if total_cameras > 0 else 0
            
            return {
                'total_cameras': total_cameras,
                'total_frames_processed': total_frames,
                'total_frames_dropped': total_dropped,
                'drop_rate': total_dropped / (total_frames + total_dropped) if (total_frames + total_dropped) > 0 else 0,
                'total_errors': total_errors,
                'total_resource_errors': total_resource_errors,
                'avg_fps': avg_fps,
                'avg_frame_processing_time_ms': avg_processing_time,
                'avg_detection_time_ms': avg_detection_time,
                'system_cpu_percent': self.system_cpu_percent,
                'process_cpu_percent': self.process.cpu_percent(interval=0.1),
                'avg_cpu_per_camera': avg_cpu_per_camera,
                'cameras_with_bottlenecks': len(bottlenecks),
                'bottlenecks': bottlenecks,
                'uptime_seconds': time.time() - self.start_time
            }


# Global singleton instance
_profiler: Optional[PerformanceProfiler] = None
_profiler_lock = threading.Lock()


def get_profiler() -> PerformanceProfiler:
    """Get or create global profiler instance"""
    global _profiler
    with _profiler_lock:
        if _profiler is None:
            _profiler = PerformanceProfiler()
        return _profiler

