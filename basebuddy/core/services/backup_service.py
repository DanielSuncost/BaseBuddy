"""
Backup management service.

Provides automatic backup of surveillance recordings to external drives
with file tracking, deduplication, and scheduled operations.
"""
import os
import json
import time
import shutil
import threading
import logging
from typing import Optional

logger = logging.getLogger('basebuddy')


class BackupManager:
    """
    Manages automatic backup of surveillance recordings to external drive.
    
    Features:
    - Automatic periodic backups
    - Only backs up files older than 5 minutes (ensures complete recordings)
    - Tracks backed up files to avoid duplicates
    - Cleans up old tracking data
    - Manual backup trigger available
    - Drive detection and setup assistance
    """
    
    def __init__(self, record_root, backup_drive_path, backup_folder,
                 backup_interval_hours=1, backup_max_age_hours=720, 
                 enabled=True):
        """
        Initialize backup manager.
        
        Args:
            record_root: Root directory containing recordings
            backup_drive_path: Path to backup drive mount point
            backup_folder: Folder name inside backup drive
            backup_interval_hours: How often to run backups (default 1)
            backup_max_age_hours: How long to keep tracking data (default 720)
            enabled: Enable/disable backup system (default True)
        """
        # Absolute path so os.walk works regardless of process cwd
        self.record_root = os.path.abspath(record_root)
        self.backup_drive_path = backup_drive_path
        self.backup_folder = backup_folder
        self.backup_interval_hours = backup_interval_hours
        self.backup_max_age_hours = backup_max_age_hours
        self.enabled = enabled
        
        self.backup_path = os.path.join(backup_drive_path, backup_folder)
        self.tracking_file = os.path.join(self.record_root, ".backup_tracking.json")
        self.last_backup_time = 0
        self.backed_up_files = self._load_tracking_data()
        self.running = False
        self.thread = None
        self._lock = threading.Lock()

    def apply_runtime_settings(
        self,
        *,
        record_root: Optional[str] = None,
        backup_drive_path: Optional[str] = None,
        backup_folder: Optional[str] = None,
        backup_interval_hours: Optional[int] = None,
        backup_max_age_hours: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        """Update settings from config without restarting the process."""
        with self._lock:
            if record_root is not None:
                self.record_root = os.path.abspath(record_root)
                self.tracking_file = os.path.join(self.record_root, ".backup_tracking.json")
                self.backed_up_files = self._load_tracking_data()
            if backup_drive_path is not None:
                self.backup_drive_path = backup_drive_path
            if backup_folder is not None:
                self.backup_folder = backup_folder
            self.backup_path = os.path.join(self.backup_drive_path, self.backup_folder)
            if backup_interval_hours is not None:
                self.backup_interval_hours = max(1, int(backup_interval_hours))
            if backup_max_age_hours is not None:
                self.backup_max_age_hours = max(1, int(backup_max_age_hours))
            if enabled is not None:
                self.enabled = bool(enabled)
        if self.enabled and not self.running:
            self.start()
        elif not self.enabled and self.running:
            self.stop()
    
    def _load_tracking_data(self):
        """Load the list of already backed up files."""
        try:
            if os.path.exists(self.tracking_file):
                with open(self.tracking_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading backup tracking data: {e}")
            return {}
    
    def _save_tracking_data(self):
        """Save the list of backed up files."""
        try:
            with open(self.tracking_file, 'w') as f:
                json.dump(self.backed_up_files, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving backup tracking data: {e}")
    
    def is_backup_drive_available(self):
        """
        Check if the backup drive is available and writable.
        
        Returns:
            bool: True if drive is available and writable
        """
        try:
            if not os.path.exists(self.backup_drive_path):
                logger.warning(f"Backup drive not found: {self.backup_drive_path}")
                return False
            
            os.makedirs(self.backup_path, exist_ok=True)
            
            # Test write access
            test_file = os.path.join(self.backup_path, ".test_write")
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
            
            return True
        except Exception as e:
            logger.error(f"Backup drive not accessible: {e}")
            return False
    
    def find_external_drives(self):
        """
        Find available external drives that could be used for backup.
        
        Returns:
            List of dictionaries with drive information
        """
        drives = []
        
        try:
            # Check /media/ directory for mounted drives
            if os.path.exists('/media'):
                for user_dir in os.listdir('/media'):
                    user_path = os.path.join('/media', user_dir)
                    if os.path.isdir(user_path):
                        for drive_dir in os.listdir(user_path):
                            drive_path = os.path.join(user_path, drive_dir)
                            if os.path.isdir(drive_path):
                                writable = self._test_drive_writable(drive_path)
                                drives.append({
                                    'path': drive_path,
                                    'name': drive_dir,
                                    'writable': writable
                                })
            
            # Check /mnt/ directory as alternative
            if os.path.exists('/mnt'):
                for drive_dir in os.listdir('/mnt'):
                    drive_path = os.path.join('/mnt', drive_dir)
                    if os.path.isdir(drive_path):
                        writable = self._test_drive_writable(drive_path)
                        drives.append({
                            'path': drive_path,
                            'name': drive_dir,
                            'writable': writable
                        })
        
        except Exception as e:
            logger.error(f"Error scanning for drives: {e}")
        
        return drives
    
    def _test_drive_writable(self, drive_path):
        """Test if a drive is writable."""
        try:
            test_file = os.path.join(drive_path, '.backup_test')
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            return True
        except Exception:
            return False
    
    def get_files_to_backup(self):
        """
        Get list of recording files that need to be backed up.
        
        Returns:
            List of file paths to backup
        """
        files_to_backup = []
        
        if not os.path.exists(self.record_root):
            return files_to_backup
        
        # Only backup files older than 5 minutes to ensure they're complete
        cutoff_time = time.time() - (5 * 60)
        
        for root, dirs, files in os.walk(self.record_root):
            for file in files:
                if file.endswith('.mp4'):
                    file_path = os.path.join(root, file)
                    
                    # Skip if already backed up
                    if file_path in self.backed_up_files:
                        continue
                    
                    # Skip if file is too new
                    if os.path.getmtime(file_path) > cutoff_time:
                        continue
                    
                    files_to_backup.append(file_path)
        
        return files_to_backup
    
    def backup_file(self, source_path):
        """
        Backup a single file to the external drive.
        
        Args:
            source_path: Path to source file
            
        Returns:
            bool: True if backup successful
        """
        try:
            rel_path = os.path.relpath(source_path, self.record_root)
            dest_path = os.path.join(self.backup_path, rel_path)
            
            dest_dir = os.path.dirname(dest_path)
            os.makedirs(dest_dir, exist_ok=True)
            
            shutil.copy2(source_path, dest_path)
            
            self.backed_up_files[source_path] = {
                'backup_time': time.time(),
                'size': os.path.getsize(source_path)
            }
            
            logger.info(f"Backed up: {rel_path}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to backup {source_path}: {e}")
            return False
    
    def perform_backup(self):
        """Perform the backup operation."""
        if not self.enabled:
            return
        
        logger.info("Starting backup process")
        
        if not self.is_backup_drive_available():
            logger.warning("Backup skipped - drive not available")
            return
        
        files_to_backup = self.get_files_to_backup()
        
        if not files_to_backup:
            logger.info("No new files to backup")
            return
        
        logger.info(f"Found {len(files_to_backup)} files to backup")
        
        backed_up_count = 0
        total_size = 0
        
        for file_path in files_to_backup:
            if self.backup_file(file_path):
                backed_up_count += 1
                total_size += os.path.getsize(file_path)
        
        self._save_tracking_data()
        self._cleanup_old_tracking()
        
        logger.info(f"Backup completed: {backed_up_count} files "
            f"({total_size / (1024*1024):.1f} MB)"
        )
    
    def _cleanup_old_tracking(self):
        """Remove tracking entries for files that no longer exist or are too old."""
        current_time = time.time()
        cutoff_time = current_time - (self.backup_max_age_hours * 3600)
        
        files_to_remove = []
        for file_path, data in self.backed_up_files.items():
            if not os.path.exists(file_path) or data.get('backup_time', 0) < cutoff_time:
                files_to_remove.append(file_path)
        
        for file_path in files_to_remove:
            del self.backed_up_files[file_path]
        
        if files_to_remove:
            logger.info(f"Cleaned up {len(files_to_remove)} old tracking entries")
            self._save_tracking_data()
    
    def _backup_loop(self):
        """Main backup loop that runs periodically."""
        while self.running:
            try:
                current_time = time.time()
                
                if current_time - self.last_backup_time >= (self.backup_interval_hours * 3600):
                    self.perform_backup()
                    self.last_backup_time = current_time
                
                time.sleep(300)  # Check every 5 minutes
            
            except Exception as e:
                logger.error(f"Backup loop error: {e}")
                time.sleep(60)
    
    def start(self):
        """Start the backup manager in a background thread."""
        if not self.enabled:
            logger.info("Backup system disabled")
            return
        
        if self.running:
            return
        
        logger.info("Starting backup manager")
        self.running = True
        self.thread = threading.Thread(target=self._backup_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the backup manager."""
        if not self.running:
            return
        
        logger.info("Stopping backup manager")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def get_status(self):
        """
        Get backup system status.
        
        Returns:
            Dictionary with status information
        """
        return {
            'enabled': self.enabled,
            'drive_available': self.is_backup_drive_available(),
            'backup_path': self.backup_path,
            'last_backup': self.last_backup_time,
            'files_backed_up': len(self.backed_up_files),
            'running': self.running
        }


