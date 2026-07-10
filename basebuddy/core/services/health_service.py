"""
Health monitoring service.

Lightweight process and camera health logger with automatic log rotation.
Monitors threads, memory, file descriptors, and camera status.
"""
import os
import time
import threading
import logging

logger = logging.getLogger('basebuddy')


class HealthMonitor:
    """
    Monitors system and camera health with periodic logging.
    
    Tracks:
    - Process threads, memory (RSS), and file descriptors
    - System load average
    - Camera frame queues and detection results
    - Recording status
    - Automatic emergency cleanup when memory exceeds thresholds
    """
    
    def __init__(self, grabbers_ref, record_root, log_dir='logs', 
                 max_bytes=5*1024*1024, interval_s=10):
        """
        Initialize health monitor.
        
        Args:
            grabbers_ref: Dictionary of camera grabbers to monitor
            record_root: Root directory for recordings
            log_dir: Directory for health logs (default 'logs')
            max_bytes: Max log size before rotation (default 5MB)
            interval_s: Seconds between health checks (default 10)
        """
        self.grabbers_ref = grabbers_ref
        self.record_root = record_root
        self.interval_s = interval_s
        self.running = False
        self.thread = None
        
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, 'health.log')
        self.max_bytes = max_bytes
    
    def start(self):
        """Start health monitoring in a background thread."""
        if self.running:
            return
        
        logger.info("Starting health monitor")
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop health monitoring."""
        if not self.running:
            return
        
        logger.info("Stopping health monitor")
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
    
    def _rotate(self):
        """Rotate log file if it exceeds max size."""
        try:
            if os.path.exists(self.log_path) and os.path.getsize(self.log_path) > self.max_bytes:
                old = self.log_path + '.1'
                if os.path.exists(old):
                    os.remove(old)
                os.rename(self.log_path, old)
        except Exception:
            pass
    
    def _write(self, text):
        """Write to health log file with rotation."""
        self._rotate()
        try:
            with open(self.log_path, 'a') as f:
                f.write(text)
        except Exception:
            pass
    
    def _proc_stats(self):
        """
        Get process statistics from /proc.
        
        Returns:
            Tuple of (threads, rss_kb, fds, utime, stime)
        """
        try:
            pid = os.getpid()
            
            # Get thread count and RSS from status
            with open(f'/proc/{pid}/status') as f:
                status = f.read()
            
            threads = 0
            rss_kb = 0
            for line in status.splitlines():
                if line.startswith('Threads:'):
                    threads = int(line.split()[-1])
                elif line.startswith('VmRSS:'):
                    rss_kb = int(line.split()[1])
            
            # Get CPU times from stat
            with open(f'/proc/{pid}/stat') as f:
                stat = f.read().split()
            utime = int(stat[13])
            stime = int(stat[14])
            
            # Count file descriptors
            fds = len(os.listdir(f'/proc/{pid}/fd'))
            
            return threads, rss_kb, fds, utime, stime
        
        except Exception:
            return 0, 0, 0, 0, 0
    
    def _sys_stats(self):
        """
        Get system load average.
        
        Returns:
            List of [1min, 5min, 15min] load averages
        """
        try:
            with open('/proc/loadavg') as f:
                la = f.read().split()[:3]
            return la
        except Exception:
            return ['0.00', '0.00', '0.00']
    
    def _check_memory_and_cleanup(self, rss_mb):
        """
        Check memory usage and trigger cleanup if needed.
        
        Args:
            rss_mb: Current RSS memory in MB
        """
        if rss_mb > 50000:  # 50GB critical threshold
            logger.critical(f"Memory usage {rss_mb:.1f}MB exceeds 50GB, triggering emergency cleanup")
            
            try:
                for cam_id, g in list(self.grabbers_ref.items()):
                    try:
                        if hasattr(g, 'clear_cached_frames'):
                            g.clear_cached_frames()
                        if hasattr(g, 'detection_queue'):
                            g.detection_queue.clear()
                        clip_buffer = getattr(g, '_clip_buffer', None)
                        if clip_buffer is not None:
                            dropped = clip_buffer.drop_all_sessions()
                            if dropped:
                                logger.warning(f"Camera {cam_id}: dropped {dropped} in-flight event clip sessions")
                    except Exception as e:
                        logger.error(f"Error cleaning camera {cam_id}: {e}")
                
                logger.info("Emergency memory cleanup completed")
            
            except Exception as e:
                logger.error(f"Emergency cleanup failed: {e}")
        
        elif rss_mb > 40000:  # 40GB warning threshold
            logger.warning(f"Memory usage high: {rss_mb:.1f}MB")
    
    def _clear_jpeg_caches(self, rss_mb, cleanup_counter):
        """
        Clear JPEG caches to prevent gradual memory buildup.
        
        Args:
            rss_mb: Current RSS memory in MB
            cleanup_counter: Counter for periodic cleanup
            
        Returns:
            Updated cleanup counter
        """
        if rss_mb > 20000:  # 20GB threshold for JPEG cache cleanup
            cleanup_counter += 1
            if cleanup_counter >= 6:  # Every 60 seconds when above 20GB
                logger.info(f"Memory {rss_mb:.1f}MB > 20GB, clearing JPEG caches")
                
                for cam_id, g in list(self.grabbers_ref.items()):
                    try:
                        with g.lock:
                            g.latest_display_jpeg_bytes = None
                            g.latest_detection_jpeg_bytes = None
                    except Exception:
                        pass
                
                cleanup_counter = 0
        
        return cleanup_counter
    
    def _loop(self):
        """Main health monitoring loop."""
        cleanup_counter = 0
        
        while self.running:
            # Get process stats
            threads, rss_kb, fds, utime, stime = self._proc_stats()
            load_avg = self._sys_stats()
            rss_mb = rss_kb / 1024
            
            # Build log lines
            lines = [
                f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n",
                f"threads={threads} rss_mb={rss_mb:.1f} fds={fds} load={','.join(load_avg)}\n"
            ]
            
            # Check memory and trigger cleanup if needed
            self._check_memory_and_cleanup(rss_mb)
            cleanup_counter = self._clear_jpeg_caches(rss_mb, cleanup_counter)
            
            # Log camera stats
            try:
                total_frames = 0
                for cam_id, g in list(self.grabbers_ref.items()):
                    dq = len(getattr(g, 'detection_queue', []))
                    fr = len(getattr(g, 'frames', []))
                    total_frames += fr
                    dr = len(getattr(g, 'detection_results', {}))
                    rec = g.is_recording() if hasattr(g, 'is_recording') else False
                    lines.append(
                        f"cam={cam_id} dq={dq} frames={fr} results={dr} recording={int(rec)}\n"
                    )
                
                lines.append(f"TOTAL: frames={total_frames}\n")
            
            except Exception:
                pass
            
            lines.append("\n")
            self._write(''.join(lines))
            
            time.sleep(self.interval_s)


