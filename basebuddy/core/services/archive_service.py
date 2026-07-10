"""
Archive service — weekly move-to-HRL + local cleanup.

Scans all media directories (recordings, stills, detection images,
timelapse outputs, video thumbnails), copies files older than a
configurable age to the external drive, then removes the local copies
to reclaim disk space.  Runs on a background thread while the app is up.
"""

import os
import json
import time
import shutil
import threading
import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger("basebuddy")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov"}
_ALL_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS


class ArchiveService:
    """
    Periodically archives local media to an external drive then deletes
    the local copies.  Designed to keep the local disk from filling up.
    """

    def __init__(
        self,
        source_dirs: dict,
        archive_drive_path: str,
        archive_folder: str,
        interval_days: int = 1,
        min_age_days: int = 2,
        enabled: bool = True,
        storage_quota_gb: float = 0.0,
    ):
        """
        Args:
            source_dirs:  mapping of label -> absolute path, e.g.
                          {"recordings": "/home/.../recordings", ...}
            archive_drive_path: mount-point of the external drive
            archive_folder:     subfolder on the drive for archives
            interval_days:      how often to run (default 7)
            min_age_days:       only archive files older than this (default 2)
            enabled:            master on/off switch
        """
        self.source_dirs = source_dirs
        self.archive_drive_path = archive_drive_path
        self.archive_folder = archive_folder
        self.archive_path = os.path.join(archive_drive_path, archive_folder)
        self.interval_seconds = interval_days * 86400
        self.min_age_seconds = min_age_days * 86400
        self.enabled = enabled
        # When total local media (sum of source_dirs) exceeds this many GiB, run
        # extra archive passes (at most once per hour). 0 = off.
        try:
            self.storage_quota_gb = max(0.0, float(storage_quota_gb))
        except (TypeError, ValueError):
            self.storage_quota_gb = 0.0
        self._last_quota_archive = 0.0
        self._lock = threading.Lock()

        from basebuddy.core.paths import get_repo_root

        self._state_file = os.path.join(get_repo_root(), ".archive_state.json")
        self._state = self._load_state()
        self.running = False
        self._thread = None

    def apply_runtime_settings(
        self,
        *,
        source_dirs: Optional[Dict[str, str]] = None,
        archive_drive_path: Optional[str] = None,
        archive_folder: Optional[str] = None,
        interval_days: Optional[int] = None,
        min_age_days: Optional[int] = None,
        enabled: Optional[bool] = None,
        storage_quota_gb: Optional[float] = None,
    ) -> None:
        """Update settings from config without restarting the process."""
        with self._lock:
            if source_dirs is not None:
                self.source_dirs = dict(source_dirs)
            if archive_drive_path is not None:
                self.archive_drive_path = archive_drive_path
            if archive_folder is not None:
                self.archive_folder = archive_folder
            self.archive_path = os.path.join(self.archive_drive_path, self.archive_folder)
            if interval_days is not None:
                self.interval_seconds = max(1, int(interval_days)) * 86400
            if min_age_days is not None:
                self.min_age_seconds = max(0, int(min_age_days) * 86400)
            if enabled is not None:
                self.enabled = bool(enabled)
            if storage_quota_gb is not None:
                try:
                    self.storage_quota_gb = max(0.0, float(storage_quota_gb))
                except (TypeError, ValueError):
                    self.storage_quota_gb = 0.0
        if self.enabled and not self.running:
            self.start()
        elif not self.enabled and self.running:
            self.stop()

    def _local_usage_bytes(self) -> int:
        from basebuddy.core.storage_usage import total_local_media_bytes

        return total_local_media_bytes(self.source_dirs)

    def _over_storage_quota(self) -> bool:
        if self.storage_quota_gb <= 0:
            return False
        used = self._local_usage_bytes()
        return used > self.storage_quota_gb * (1024**3)

    # -- persistence ---------------------------------------------------------

    def _load_state(self) -> dict:
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file, "r") as fh:
                    return json.load(fh)
        except Exception as exc:
            logger.error("Failed to load archive state: %s", exc)
        return {"last_run": 0, "total_archived": 0, "total_freed_bytes": 0}

    def _save_state(self):
        try:
            with open(self._state_file, "w") as fh:
                json.dump(self._state, fh, indent=2)
        except Exception as exc:
            logger.error("Failed to save archive state: %s", exc)

    # -- drive checks --------------------------------------------------------

    def is_drive_available(self) -> bool:
        try:
            if not os.path.exists(self.archive_drive_path):
                return False
            os.makedirs(self.archive_path, exist_ok=True)
            probe = os.path.join(self.archive_path, ".write_test")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            return True
        except Exception:
            return False

    # -- core archive logic --------------------------------------------------

    def _collect_files(self):
        """Yield (source_path, label, rel_path) for every archivable file."""
        cutoff = time.time() - self.min_age_seconds
        for label, src_dir in self.source_dirs.items():
            if not os.path.isdir(src_dir):
                continue
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in _ALL_MEDIA_EXTS:
                        continue
                    full = os.path.join(root, fname)
                    try:
                        if os.path.getmtime(full) > cutoff:
                            continue
                    except OSError:
                        continue
                    rel = os.path.relpath(full, src_dir)
                    yield full, label, rel

    def _archive_file(self, src: str, label: str, rel: str) -> int:
        """Copy *src* to HRL under <archive>/<label>/<rel>, then delete local.

        Returns bytes freed (0 on failure).
        """
        dest = os.path.join(self.archive_path, label, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            size = os.path.getsize(src)
            shutil.copy2(src, dest)
            if os.path.getsize(dest) != size:
                logger.warning("Size mismatch after copy: %s", src)
                return 0
            os.remove(src)
            return size
        except Exception as exc:
            logger.error("Archive failed for %s: %s", src, exc)
            return 0

    @staticmethod
    def _prune_empty_dirs(directory: str):
        """Walk bottom-up and remove any empty directories."""
        if not os.path.isdir(directory):
            return
        for root, dirs, files in os.walk(directory, topdown=False):
            for d in dirs:
                path = os.path.join(root, d)
                try:
                    if not os.listdir(path):
                        os.rmdir(path)
                except OSError:
                    pass

    def perform_archive(self) -> dict:
        """Run one full archive pass.  Returns summary dict."""
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}

        if not self.is_drive_available():
            logger.warning("Archive skipped — drive not available at %s",
                           self.archive_drive_path)
            return {"skipped": True, "reason": "drive_unavailable"}

        logger.info("Archive pass starting")
        archived = 0
        freed = 0

        for src, label, rel in self._collect_files():
            n = self._archive_file(src, label, rel)
            if n:
                archived += 1
                freed += n

        for src_dir in self.source_dirs.values():
            self._prune_empty_dirs(src_dir)

        self._state["last_run"] = time.time()
        self._state["total_archived"] = self._state.get("total_archived", 0) + archived
        self._state["total_freed_bytes"] = self._state.get("total_freed_bytes", 0) + freed
        self._save_state()

        freed_mb = freed / (1024 * 1024)
        logger.info("Archive pass done: %d files, %.1f MB freed", archived, freed_mb)
        if archived == 0:
            logger.info(
                "Archive: no files moved (need mtime older than min_age_days=%d; "
                "next scan in %d day(s))",
                self.min_age_seconds // 86400,
                self.interval_seconds // 86400,
            )
        return {"archived": archived, "freed_mb": round(freed_mb, 2)}

    # -- background scheduler ------------------------------------------------

    def _loop(self):
        while self.running:
            try:
                if not self.enabled:
                    time.sleep(30)
                    continue
                now = time.time()
                elapsed = now - self._state.get("last_run", 0)
                scheduled = elapsed >= self.interval_seconds
                quota_force = False
                if self._over_storage_quota() and (now - self._last_quota_archive >= 3600):
                    quota_force = True
                if scheduled or quota_force:
                    if quota_force and not scheduled:
                        logger.warning(
                            "Storage quota exceeded (%.1f GiB); running archive pass",
                            self.storage_quota_gb,
                        )
                        self._last_quota_archive = now
                    self.perform_archive()
                time.sleep(300)
            except Exception as exc:
                logger.error("Archive loop error: %s", exc)
                time.sleep(60)

    def start(self):
        if not self.enabled:
            logger.info("Archive service disabled")
            return
        if self.running:
            return
        logger.info("Starting archive service (interval=%dd, min_age=%dd)",
                     self.interval_seconds // 86400,
                     self.min_age_seconds // 86400)
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping archive service")
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    # -- status --------------------------------------------------------------

    def get_status(self) -> dict:
        last = self._state.get("last_run", 0)
        next_run = last + self.interval_seconds if last else 0
        return {
            "enabled": self.enabled,
            "running": self.running,
            "drive_available": self.is_drive_available(),
            "archive_path": self.archive_path,
            "interval_days": self.interval_seconds // 86400,
            "min_age_days": self.min_age_seconds // 86400,
            "storage_quota_gb": self.storage_quota_gb,
            "last_run": datetime.fromtimestamp(last).isoformat() if last else None,
            "next_run": datetime.fromtimestamp(next_run).isoformat() if next_run else None,
            "total_archived": self._state.get("total_archived", 0),
            "total_freed_mb": round(
                self._state.get("total_freed_bytes", 0) / (1024 * 1024), 2
            ),
            "source_dirs": self.source_dirs,
        }
