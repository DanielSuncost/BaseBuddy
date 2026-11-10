"""
Backup management service.

Copies finished surveillance recordings to an external drive and keeps
BaseBuddy's footprint on that drive inside an adaptive budget (see
basebuddy.core.storage_budget): oldest backups are pruned before each copy so
the drive never fills, and the budget is recomputed every cycle from the
drive's real capacity, free space, and other (non-BaseBuddy) usage.
"""
import errno
import os
import json
import time
import shutil
import threading
import logging
from typing import Optional

from basebuddy.core.storage_budget import (
    BudgetSettings,
    drive_budget_for,
    drive_state,
    round_up,
    settings_from_config,
    GIB,
)

logger = logging.getLogger('basebuddy')


class BackupManager:
    """
    Manages automatic backup of surveillance recordings to external drive.

    Features:
    - Periodic backups of .mp4 files older than 5 minutes
    - Tracks backed up files to avoid duplicates
    - Adaptive storage budget with oldest-first pruning (dry-run capable)
    - Distinct drive states: ok / missing / read_only / full
    - Status with last success / last error / consecutive failures / budget
    """

    def __init__(self, record_root, backup_drive_path, backup_folder,
                 backup_interval_hours=1, backup_max_age_hours=720,
                 enabled=True, budget_settings: Optional[BudgetSettings] = None):
        # Absolute path so os.walk works regardless of process cwd
        self.record_root = os.path.abspath(record_root)
        self.backup_drive_path = backup_drive_path
        self.backup_folder = backup_folder
        self.backup_interval_hours = backup_interval_hours
        self.backup_max_age_hours = backup_max_age_hours
        self.enabled = enabled
        try:
            self.budget_settings = (budget_settings or settings_from_config()).normalized()
        except Exception:
            self.budget_settings = BudgetSettings()

        self.backup_path = os.path.join(backup_drive_path, backup_folder)
        self.tracking_file = os.path.join(self.record_root, ".backup_tracking.json")
        self.state_file = os.path.join(self.record_root, ".backup_state.json")
        self.last_backup_time = 0  # last attempt (kept for compatibility)
        self.backed_up_files = self._load_tracking_data()
        self._st = self._load_state()
        self.running = False
        self.thread = None
        self._lock = threading.Lock()
        self._drive_budget = None

    # ------------------------------------------------------------------ config
    def apply_runtime_settings(
        self,
        *,
        record_root: Optional[str] = None,
        backup_drive_path: Optional[str] = None,
        backup_folder: Optional[str] = None,
        backup_interval_hours: Optional[int] = None,
        backup_max_age_hours: Optional[int] = None,
        enabled: Optional[bool] = None,
        budget_settings: Optional[BudgetSettings] = None,
    ) -> None:
        """Update settings from config without restarting the process."""
        with self._lock:
            if record_root is not None:
                self.record_root = os.path.abspath(record_root)
                self.tracking_file = os.path.join(self.record_root, ".backup_tracking.json")
                self.state_file = os.path.join(self.record_root, ".backup_state.json")
                self.backed_up_files = self._load_tracking_data()
            if backup_drive_path is not None:
                self.backup_drive_path = backup_drive_path
            if backup_folder is not None:
                self.backup_folder = backup_folder
            new_path = os.path.join(self.backup_drive_path, self.backup_folder)
            if new_path != self.backup_path:
                self._drive_budget = None
            self.backup_path = new_path
            if backup_interval_hours is not None:
                self.backup_interval_hours = max(1, int(backup_interval_hours))
            if backup_max_age_hours is not None:
                self.backup_max_age_hours = max(1, int(backup_max_age_hours))
            if enabled is not None:
                self.enabled = bool(enabled)
            if budget_settings is not None:
                self.budget_settings = budget_settings.normalized()
        if self.enabled and not self.running:
            self.start()
        elif not self.enabled and self.running:
            self.stop()

    # ------------------------------------------------------------- persistence
    def _load_tracking_data(self):
        try:
            if os.path.exists(self.tracking_file):
                with open(self.tracking_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading backup tracking data: {e}")
            return {}

    def _save_tracking_data(self):
        try:
            with open(self.tracking_file, 'w') as f:
                json.dump(self.backed_up_files, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving backup tracking data: {e}")

    def _load_state(self) -> dict:
        st = {}
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r') as f:
                    st = json.load(f) or {}
        except Exception as e:
            logger.error(f"Error loading backup state: {e}")
        if not st.get("last_success"):
            # Seed from tracking data so status is meaningful after upgrade.
            times = [v.get('backup_time', 0) for v in self.backed_up_files.values()
                     if isinstance(v, dict)]
            st["last_success"] = max(times) if times else 0
        st.setdefault("last_error", None)
        st.setdefault("last_error_time", 0)
        st.setdefault("consecutive_failures", 0)
        st.setdefault("drive_state", "unknown")
        st.setdefault("drive_reason", "")
        st.setdefault("files_ok_total", 0)
        st.setdefault("files_failed_total", 0)
        st.setdefault("bytes_pruned_total", 0)
        st.setdefault("last_run", {})
        return st

    def _save_state(self):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(self._st, f, indent=2)
            os.replace(tmp, self.state_file)
        except Exception as e:
            logger.error(f"Error saving backup state: {e}")

    def _record_error(self, msg: str):
        self._st["last_error"] = msg
        self._st["last_error_time"] = time.time()
        self._st["consecutive_failures"] = int(self._st.get("consecutive_failures", 0)) + 1
        self._save_state()

    # ------------------------------------------------------------ drive/budget
    def drive_budget(self):
        if self._drive_budget is None and os.path.isdir(self.backup_drive_path):
            db = drive_budget_for(self.backup_drive_path)
            manifest_path = os.path.join(self.record_root, ".backup_manifest.json")
            db.register(self.backup_path, manifest_path)
            self._drive_budget = db
        return self._drive_budget

    def drive_state(self, need_bytes: int = 0):
        return drive_state(self.backup_drive_path, self.backup_path, need_bytes)

    def is_backup_drive_available(self):
        """True when the drive is mounted and writable (a *full* drive counts:
        it is handled by pruning rather than by skipping the backup)."""
        state, reason = self.drive_state()
        self._st["drive_state"], self._st["drive_reason"] = state, reason
        if state in ("missing", "read_only"):
            logger.warning(f"Backup drive {state}: {reason}")
            return False
        return True

    def _ensure_space(self, need_bytes: int, factor: float = 1.0) -> dict:
        db = self.drive_budget()
        if db is None:
            return {"skipped": True, "reason": "no_drive"}
        try:
            plan = db.prune(self.budget_settings, need_bytes=int(need_bytes * factor))
        except Exception as exc:
            logger.error(f"Backup prune failed: {exc}")
            return {"skipped": True, "reason": f"error: {exc}"}
        if not plan.get("dry_run") and plan.get("deleted_bytes"):
            self._st["bytes_pruned_total"] = int(self._st.get("bytes_pruned_total", 0)) + int(plan["deleted_bytes"])
        if not plan.get("skipped"):
            self._st["last_prune"] = {k: v for k, v in plan.items() if k != "sample"}
            self._st["last_prune"]["at"] = time.time()
        return plan

    # ------------------------------------------------------------------ backup
    def get_files_to_backup(self):
        files_to_backup = []
        if not os.path.exists(self.record_root):
            return files_to_backup
        cutoff_time = time.time() - (5 * 60)
        for root, dirs, files in os.walk(self.record_root):
            for file in files:
                if file.endswith('.mp4'):
                    file_path = os.path.join(root, file)
                    if file_path in self.backed_up_files:
                        continue
                    try:
                        if os.path.getmtime(file_path) > cutoff_time:
                            continue
                    except OSError:
                        continue
                    files_to_backup.append(file_path)
        files_to_backup.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
        return files_to_backup

    def _copy(self, source_path, dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy2(source_path, dest_path)

    def backup_file(self, source_path):
        """Backup one file: make room first, copy, and on ENOSPC prune + retry once."""
        try:
            size = os.path.getsize(source_path)
            mtime = os.path.getmtime(source_path)
        except OSError as e:
            logger.error(f"Failed to stat {source_path}: {e}")
            return False
        rel_path = os.path.relpath(source_path, self.record_root)
        dest_path = os.path.join(self.backup_path, rel_path)
        self._ensure_space(size)
        for attempt in (1, 2):
            try:
                self._copy(source_path, dest_path)
                self.backed_up_files[source_path] = {'backup_time': time.time(), 'size': size}
                db = self.drive_budget()
                if db is not None:
                    m = db.roots.get(self.backup_path)
                    if m is not None:
                        m.add(rel_path, size, mtime)
                logger.info(f"Backed up: {rel_path}")
                return True
            except OSError as e:
                if e.errno == errno.ENOSPC and attempt == 1:
                    logger.warning(f"Backup drive full while copying {rel_path}; pruning and retrying once")
                    self._remove_partial(dest_path, size)
                    self._ensure_space(size, factor=2.0)
                    continue
                if e.errno == errno.ENOSPC:
                    self._remove_partial(dest_path, size)
                    self._st["drive_state"] = "full"
                    self._st["drive_reason"] = "no space left on device after prune+retry"
                logger.error(f"Failed to backup {rel_path}: {e}")
                self._last_file_error = f"{rel_path}: {e}"
                return False
            except Exception as e:
                logger.error(f"Failed to backup {rel_path}: {e}")
                self._last_file_error = f"{rel_path}: {e}"
                return False
        return False

    def _remove_partial(self, dest_path, expected_size):
        """Remove a truncated copy we just created (only inside our backup folder)."""
        try:
            real_root = os.path.realpath(self.backup_path) + os.sep
            if os.path.realpath(dest_path).startswith(real_root) and os.path.isfile(dest_path):
                if os.path.getsize(dest_path) != expected_size:
                    os.remove(dest_path)
        except OSError:
            pass

    def perform_backup(self):
        """Perform the backup operation."""
        if not self.enabled:
            return
        logger.info("Starting backup process")
        started = time.time()
        self._last_file_error = None

        state, reason = self.drive_state()
        self._st["drive_state"], self._st["drive_reason"] = state, reason
        if state in ("missing", "read_only"):
            logger.warning(f"Backup skipped - drive {state}: {reason}")
            self._record_error(f"drive {state}: {reason}")
            self._st["last_run"] = {"at": started, "result": f"skipped_{state}"}
            self._save_state()
            return

        db = self.drive_budget()
        if db is not None and not db.ready():
            logger.info("Backup: storage manifest is being built in the background")

        # Enforce the budget every cycle, even when there is nothing new to copy.
        self._ensure_space(0)

        files_to_backup = self.get_files_to_backup()
        if not files_to_backup:
            logger.info("No new files to backup")
            self._st["last_run"] = {"at": started, "result": "nothing_to_do"}
            if state == "ok":
                self._st["consecutive_failures"] = 0
            self._save_state()
            return

        logger.info(f"Found {len(files_to_backup)} files to backup")
        ok_count, fail_count, total_size = 0, 0, 0
        for file_path in files_to_backup:
            if self.backup_file(file_path):
                ok_count += 1
                try:
                    total_size += os.path.getsize(file_path)
                except OSError:
                    pass
            else:
                fail_count += 1

        self._save_tracking_data()
        self._cleanup_old_tracking()
        if db is not None:
            for m in db.roots.values():
                m.save()

        self._st["files_ok_total"] = int(self._st.get("files_ok_total", 0)) + ok_count
        self._st["files_failed_total"] = int(self._st.get("files_failed_total", 0)) + fail_count
        self._st["last_run"] = {"at": started, "ok": ok_count, "failed": fail_count,
                                "bytes": total_size, "result": "ok" if not fail_count else "partial"}
        if ok_count:
            self._st["last_success"] = time.time()
        if fail_count:
            self._record_error(f"{fail_count} of {len(files_to_backup)} files failed"
                               + (f" (last: {self._last_file_error})" if self._last_file_error else ""))
            logger.error(f"Backup finished with failures: {ok_count} ok, {fail_count} failed "
                         f"({total_size / (1024*1024):.1f} MB copied)")
        else:
            self._st["consecutive_failures"] = 0
            self._save_state()
            logger.info(f"Backup completed: {ok_count} files ({total_size / (1024*1024):.1f} MB)")

    def _cleanup_old_tracking(self):
        """Remove tracking entries for files that no longer exist or are too old."""
        cutoff_time = time.time() - (self.backup_max_age_hours * 3600)
        files_to_remove = [p for p, d in self.backed_up_files.items()
                           if not os.path.exists(p) or d.get('backup_time', 0) < cutoff_time]
        for p in files_to_remove:
            del self.backed_up_files[p]
        if files_to_remove:
            logger.info(f"Cleaned up {len(files_to_remove)} old tracking entries")
            self._save_tracking_data()

    def _backup_loop(self):
        while self.running:
            try:
                now = time.time()
                if now - self.last_backup_time >= (self.backup_interval_hours * 3600):
                    self.perform_backup()
                    self.last_backup_time = now
                time.sleep(300)
            except Exception as e:
                logger.error(f"Backup loop error: {e}")
                try:
                    self._record_error(f"loop error: {e}")
                except Exception:
                    pass
                time.sleep(60)

    def start(self):
        if not self.enabled:
            logger.info("Backup system disabled")
            return
        if self.running:
            return
        logger.info("Starting backup manager (prune dry_run=%s)" % self.budget_settings.dry_run)
        self.running = True
        self.thread = threading.Thread(target=self._backup_loop, daemon=True)
        self.thread.start()

    def stop(self):
        if not self.running:
            return
        logger.info("Stopping backup manager")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

    # ------------------------------------------------------------------ status
    def budget_snapshot(self) -> dict:
        db = self.drive_budget()
        if db is None:
            return {}
        try:
            return db.budget(self.budget_settings)
        except OSError as exc:
            return {"error": str(exc)}

    def storage_health(self) -> dict:
        """Compact ok|warning|critical summary for /health."""
        state, reason = self.drive_state()
        b = self.budget_snapshot() if state != "missing" else {}
        now = time.time()
        last_success = float(self._st.get("last_success") or 0)
        fails = int(self._st.get("consecutive_failures") or 0)
        interval = max(1, int(self.backup_interval_hours)) * 3600
        reasons, level = [], "ok"

        def bump(lvl, why):
            nonlocal level
            order = {"ok": 0, "warning": 1, "critical": 2}
            if order[lvl] > order[level]:
                level = lvl
            reasons.append(why)

        if not self.enabled:
            return {"level": "ok", "reason": "backup disabled", "enabled": False}
        if state in ("missing", "read_only"):
            bump("critical", f"backup drive {state}: {reason}")
        elif state == "full" and b.get("manifest_ready") is False:
            bump("critical", "backup drive full; storage manifest still building, pruning pending")
        elif state == "full":
            bump("critical", "backup drive full")
        if fails >= 2:
            bump("critical", f"{fails} consecutive backup failures: {self._st.get('last_error')}")
        elif fails == 1:
            bump("warning", f"last backup run failed: {self._st.get('last_error')}")
        if last_success and now - last_success > 3 * interval:
            bump("critical" if now - last_success > 6 * interval else "warning",
                 f"no successful backup for {(now - last_success) / 3600:.1f} h")
        if b and not b.get("error"):
            if b.get("free_shortfall_bytes"):
                bump("warning", f"free space {b['free_bytes'] / GIB:.1f} GiB below floor {b['floor_bytes'] / GIB:.1f} GiB")
            if b.get("over_budget_bytes"):
                bump("warning", f"BaseBuddy usage {b['over_budget_bytes'] / GIB:.1f} GiB over budget")
            if b.get("crowded_out"):
                bump("warning", f"crowded_out: other data on the drive ({b['other_bytes'] / GIB:.0f} GiB) "
                                f"limits BaseBuddy to {b['budget_bytes'] / GIB:.0f} GiB")
        if self.budget_settings.dry_run:
            lp = self._st.get("last_prune") or {}
            if lp.get("dry_run") and lp.get("deleted_bytes"):
                bump("warning", f"prune is in DRY-RUN: would delete {lp['deleted_files']} files "
                                f"({lp['deleted_bytes'] / GIB:.1f} GiB); set BACKUP_PRUNE_DRY_RUN=false to enable")
        return {
            "level": level,
            "reason": "; ".join(reasons) if reasons else "ok",
            "drive_state": state,
            "free_gb": round(b.get("free_bytes", 0) / GIB, 2) if b else None,
            "budget_gb": round(b.get("budget_bytes", 0) / GIB, 2) if b else None,
            "basebuddy_gb": round(b.get("own_bytes", 0) / GIB, 2) if b else None,
            "other_gb": round(b.get("other_bytes", 0) / GIB, 2) if b else None,
            "crowded_out": bool(b.get("crowded_out")) if b else False,
            "manifest_ready": b.get("manifest_ready") if b else None,
            "last_success": last_success or None,
            "consecutive_failures": fails,
            "prune_dry_run": self.budget_settings.dry_run,
        }

    def get_status(self):
        state, reason = self.drive_state()
        b = self.budget_snapshot() if state != "missing" else {}
        return {
            'enabled': self.enabled,
            'drive_available': state in ("ok", "full"),
            'drive_state': state,
            'drive_reason': reason,
            'backup_path': self.backup_path,
            'last_backup': self.last_backup_time,          # last attempt
            'last_attempt': self.last_backup_time,
            'last_success': self._st.get("last_success") or None,
            'last_error': self._st.get("last_error"),
            'last_error_time': self._st.get("last_error_time") or None,
            'consecutive_failures': self._st.get("consecutive_failures", 0),
            'last_run': self._st.get("last_run"),
            'files_backed_up': len(self.backed_up_files),  # tracking entries (rolling window)
            'files_ok_total': self._st.get("files_ok_total", 0),
            'files_failed_total': self._st.get("files_failed_total", 0),
            'bytes_pruned_total': self._st.get("bytes_pruned_total", 0),
            'last_prune': self._st.get("last_prune"),
            'budget': b,
            'prune_dry_run': self.budget_settings.dry_run,
            'running': self.running,
        }
