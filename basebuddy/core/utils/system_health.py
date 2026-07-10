"""
System resource health warnings (CPU / memory thresholds).

Logs warnings when system resources reach critical levels.
Detailed GPU/system stats for the Resources UI page live in
basebuddy.modules.resource_monitor.
"""
import time
import threading
import logging
import psutil

logger = logging.getLogger('basebuddy')

_stop_event = threading.Event()


def monitor_resources(check_interval=30, cpu_threshold=90, memory_threshold=90):
    """
    Monitor system resources and log warnings for high usage.
    
    Runs in a background thread until stop_resource_monitor() is called.
    
    Args:
        check_interval: Seconds between checks (default 30)
        cpu_threshold: CPU usage % to trigger warning (default 90)
        memory_threshold: Memory usage % to trigger warning (default 90)
    """
    while not _stop_event.is_set():
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            
            if memory.percent > memory_threshold:
                logger.warning(f"High memory usage: {memory.percent}% "
                    f"({memory.used / (1024**3):.2f}GB used)"
                )
            
            if cpu_percent > cpu_threshold:
                logger.warning(f"High CPU usage: {cpu_percent}%")
            
            _stop_event.wait(check_interval)
        except Exception as e:
            logger.error(f"Resource monitoring error: {e}")
            _stop_event.wait(60)


def start_resource_monitor(check_interval=30):
    """
    Start resource monitoring in a background daemon thread.
    
    Args:
        check_interval: Seconds between checks (default 30)
        
    Returns:
        Thread object
    """
    _stop_event.clear()
    monitor_thread = threading.Thread(
        target=monitor_resources,
        args=(check_interval,),
        daemon=True
    )
    monitor_thread.start()
    logger.info("Resource monitoring started")
    return monitor_thread


def stop_resource_monitor():
    """Signal the monitor thread to exit."""
    _stop_event.set()


def get_system_info():
    """
    Get current system resource information.
    
    Returns:
        Dictionary with CPU, memory, and disk metrics
    """
    return {
        'cpu_percent': psutil.cpu_percent(interval=0.1),
        'cpu_count': psutil.cpu_count(),
        'memory_percent': psutil.virtual_memory().percent,
        'memory_used_gb': round(psutil.virtual_memory().used / (1024**3), 2),
        'memory_total_gb': round(psutil.virtual_memory().total / (1024**3), 2),
        'disk_free_gb': round(psutil.disk_usage('/').free / (1024**3), 2),
        'disk_total_gb': round(psutil.disk_usage('/').total / (1024**3), 2),
        'disk_percent': psutil.disk_usage('/').percent
    }


