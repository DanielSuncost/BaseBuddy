"""
Storage management service.

Handles recording storage calculations, cleanup of old files,
and storage metrics.
"""
import os
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('basebuddy')


class StorageService:
    """Service for managing recording storage."""
    
    def __init__(self, record_root, retention_days=30):
        """
        Initialize storage service.
        
        Args:
            record_root: Root directory containing recordings
            retention_days: Days to keep recordings (0 = keep forever)
        """
        self.record_root = record_root
        self.retention_days = retention_days
    
    def calculate_recording_size(self):
        """
        Calculate total size of all recordings.
        
        Returns:
            Total size in bytes
        """
        total_size = 0
        
        if not os.path.exists(self.record_root):
            return total_size
        
        for root, dirs, files in os.walk(self.record_root):
            for file in files:
                if file.endswith(('.avi', '.mp4')):
                    file_path = os.path.join(root, file)
                    try:
                        total_size += os.path.getsize(file_path)
                    except Exception:
                        pass
        
        return total_size
    
    def cleanup_old_recordings(self, thumbnail_dir='static/recording_thumbnails'):
        """
        Clean up recordings older than retention period.
        
        Args:
            thumbnail_dir: Directory containing thumbnails
            
        Returns:
            Dictionary with cleanup results:
            - deleted_count: Number of files deleted
            - freed_space_mb: Space freed in MB
        """
        if not os.path.exists(self.record_root) or self.retention_days <= 0:
            return {"deleted_count": 0, "freed_space_mb": 0}
        
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0
        freed_space = 0
        
        logger.info(f"Cleaning up recordings older than {self.retention_days} days")
        
        for root, dirs, files in os.walk(self.record_root):
            for file in files:
                if file.endswith(('.avi', '.mp4')):
                    file_path = os.path.join(root, file)
                    try:
                        stat = os.stat(file_path)
                        file_date = datetime.fromtimestamp(stat.st_mtime)
                        
                        if file_date < cutoff_date:
                            file_size = stat.st_size
                            os.remove(file_path)
                            deleted_count += 1
                            freed_space += file_size
                            logger.info(f"Deleted old recording: {file_path} ({file_size / (1024*1024):.1f} MB)")
                            
                            # Remove corresponding thumbnail
                            if thumbnail_dir:
                                thumb_path = os.path.join(thumbnail_dir, f"thumb_{file}.jpg")
                                if os.path.exists(thumb_path):
                                    try:
                                        os.remove(thumb_path)
                                    except Exception:
                                        pass
                    
                    except Exception as e:
                        logger.error(f"Error cleaning up {file_path}: {e}")
        
        freed_space_mb = freed_space / (1024 * 1024)
        logger.info(f"Cleanup complete: {deleted_count} files deleted, {freed_space_mb:.1f} MB freed")
        
        return {
            "deleted_count": deleted_count,
            "freed_space_mb": round(freed_space_mb, 2)
        }
    
    def get_storage_stats(self):
        """
        Get storage statistics.
        
        Returns:
            Dictionary with storage metrics
        """
        import psutil
        
        recording_size_bytes = self.calculate_recording_size()
        disk = psutil.disk_usage('/')
        
        return {
            'recordings': {
                'size_bytes': recording_size_bytes,
                'size_mb': round(recording_size_bytes / (1024**2), 2),
                'size_gb': round(recording_size_bytes / (1024**3), 2)
            },
            'disk': {
                'total_gb': round(disk.total / (1024**3), 2),
                'used_gb': round(disk.used / (1024**3), 2),
                'free_gb': round(disk.free / (1024**3), 2),
                'percent_used': round(disk.percent, 1)
            },
            'retention_days': self.retention_days
        }


