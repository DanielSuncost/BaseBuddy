"""
Per-service retention enforcement and optional cloud offload.

Runs on a background thread: deletes local files past local_days; optionally
uploads to S3/R2 before delete when remote_days > 0 and a backend is active.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("basebuddy")

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
_VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov"}
_ALL_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS

# Prefer reclaiming detection crops first (largest / most ephemeral), then stills.
_DISK_PRESSURE_CATEGORIES = ("detections", "stills", "video_thumbs", "recording_thumbs")

# Subtrees that follow the plants window, not the stills or detections window.
_PLANT_DIR_NAMES = frozenset({"sam_growth", "plant_health"})


class RetentionService:
    def __init__(
        self,
        source_dirs: Dict[str, str],
        retention_policy: Dict[str, Dict[str, int]],
        remote_backend=None,
        remote_backend_kind: str = "none",
        scan_interval_seconds: int = 3600,
        enabled: bool = True,
        disk_free_min_gb: float = 20.0,
    ):
        self.source_dirs = dict(source_dirs)
        self.retention_policy = retention_policy
        self.remote_backend = remote_backend
        self.remote_backend_kind = remote_backend_kind
        self.scan_interval_seconds = max(300, int(scan_interval_seconds))
        self.enabled = enabled
        try:
            self.disk_free_min_gb = max(0.0, float(disk_free_min_gb))
        except (TypeError, ValueError):
            self.disk_free_min_gb = 20.0
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        from basebuddy.core.paths import get_repo_root

        self._state_file = os.path.join(get_repo_root(), ".retention_state.json")
        self._state = self._load_state()

    def apply_runtime_settings(
        self,
        *,
        source_dirs: Optional[Dict[str, str]] = None,
        retention_policy: Optional[Dict[str, Dict[str, int]]] = None,
        remote_backend=None,
        remote_backend_kind: Optional[str] = None,
        enabled: Optional[bool] = None,
        disk_free_min_gb: Optional[float] = None,
    ) -> None:
        with self._lock:
            if source_dirs is not None:
                self.source_dirs = dict(source_dirs)
            if retention_policy is not None:
                self.retention_policy = retention_policy
            if remote_backend is not None:
                self.remote_backend = remote_backend
            if remote_backend_kind is not None:
                self.remote_backend_kind = remote_backend_kind
            if enabled is not None:
                self.enabled = bool(enabled)
            if disk_free_min_gb is not None:
                try:
                    self.disk_free_min_gb = max(0.0, float(disk_free_min_gb))
                except (TypeError, ValueError):
                    pass
        if self.enabled and not self.running:
            self.start()
        elif not self.enabled and self.running:
            self.stop()

    def _load_state(self) -> dict:
        try:
            if os.path.isfile(self._state_file):
                with open(self._state_file, "r", encoding="utf-8") as fh:
                    return json.load(fh)
        except Exception as exc:
            logger.error("Failed to load retention state: %s", exc)
        return {"last_run": 0, "uploads": {}, "totals": {"deleted": 0, "uploaded": 0, "remote_purged": 0}}

    def _save_state(self) -> None:
        try:
            with open(self._state_file, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
        except Exception as exc:
            logger.error("Failed to save retention state: %s", exc)

    def _uploads_map(self) -> dict:
        uploads = self._state.setdefault("uploads", {})
        return uploads

    @staticmethod
    def _hide_plant_dirs(dirs: List[str]) -> None:
        dirs[:] = [d for d in dirs if d not in _PLANT_DIR_NAMES]

    def _iter_media_files(self, src_dir: str, cutoff: float, *, skip_plants: bool):
        if not os.path.isdir(src_dir):
            return
        for root, dirs, files in os.walk(src_dir):
            if skip_plants:
                self._hide_plant_dirs(dirs)
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _ALL_MEDIA_EXTS:
                    continue
                full = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime > cutoff:
                        continue
                except OSError:
                    continue
                yield full, mtime

    def _iter_plant_files(self, src_dir: str, cutoff: float):
        """Yield aged files under sam_growth/ and plant_health/ only."""
        if not os.path.isdir(src_dir):
            return
        plant_roots: List[str] = []
        for root, dirs, _files in os.walk(src_dir):
            plant_roots.extend(
                os.path.join(root, d) for d in dirs if d in _PLANT_DIR_NAMES
            )
            self._hide_plant_dirs(dirs)
        for plant_root in plant_roots:
            for full, mtime in self._iter_media_files(plant_root, cutoff, skip_plants=False):
                yield full, mtime

    def _iter_category_files(self, category: str, src_dir: str, max_age_seconds: int):
        if not os.path.isdir(src_dir):
            return
        cutoff = time.time() - max_age_seconds
        if category == "plants":
            pairs = self._iter_plant_files(src_dir, cutoff)
        else:
            pairs = self._iter_media_files(src_dir, cutoff, skip_plants=True)
        for full, mtime in pairs:
            rel = os.path.relpath(full, src_dir)
            yield full, rel, mtime

    def _should_upload(self, category: str) -> bool:
        cfg = self.retention_policy.get(category) or {}
        return (
            self.remote_backend is not None
            and getattr(self.remote_backend, "is_active", False)
            and int(cfg.get("remote_days") or 0) > 0
        )

    def _upload_key(self, category: str, rel_path: str) -> str:
        return f"{category}:{rel_path}"

    def _remote_days(self, category: str) -> int:
        return int((self.retention_policy.get(category) or {}).get("remote_days") or 0)

    def _object_age_ts(self, meta: dict) -> float:
        """Age basis for rolling cloud buffer — prefer original file mtime."""
        cm = meta.get("content_mtime")
        if cm:
            return float(cm)
        uploaded_at = meta.get("uploaded_at")
        try:
            if uploaded_at:
                return datetime.fromisoformat(uploaded_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        return 0.0

    def _list_remote_objects(self, category: str = "") -> list:
        fn = getattr(self.remote_backend, "list_objects", None)
        if not fn:
            return []
        try:
            return fn(category=category) or []
        except Exception as exc:
            logger.error("list_objects failed: %s", exc)
            return []

    @staticmethod
    def _is_quota_error(err: Optional[str]) -> bool:
        s = (err or "").lower()
        return "quota_exceeded" in s or "quota" in s

    def _maybe_upload(
        self,
        category: str,
        local_path: str,
        rel_path: str,
        content_mtime: float,
    ) -> Tuple[bool, Optional[str]]:
        if not self._should_upload(category):
            return True, None
        key = self._upload_key(category, rel_path)
        uploads = self._uploads_map()
        if key in uploads:
            return True, uploads[key].get("remote_key")

        size = 0
        try:
            size = os.path.getsize(local_path)
        except OSError:
            pass

        ok, remote_key = self.remote_backend.upload_file(local_path, category, rel_path)
        if not ok and self._is_quota_error(remote_key):
            evicted = self._evict_for_quota(need_bytes=size, category=category)
            if evicted:
                ok, remote_key = self.remote_backend.upload_file(local_path, category, rel_path)

        if ok:
            uploads[key] = {
                "remote_key": remote_key,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "content_mtime": content_mtime,
                "size": size,
            }
            return True, remote_key
        return False, remote_key

    def _evict_for_quota(self, need_bytes: int, category: str = "") -> int:
        """
        Free cloud space when quota blocks upload.
        1) Delete objects past their cloud buffer (remote_days).
        2) If still over quota, delete oldest objects (LRU) until need_bytes fits.
        """
        if self.remote_backend is None or not getattr(self.remote_backend, "is_active", False):
            return 0
        evicted = 0
        evicted += self._purge_cloud_buffer()

        usage_fn = getattr(self.remote_backend, "fetch_usage", None)
        usage = usage_fn() if usage_fn else {}
        quota_gb = float(usage.get("quota_gb") or 0)
        used_bytes = int(usage.get("used_bytes") or 0)
        if quota_gb <= 0:
            return evicted
        quota_bytes = int(quota_gb * (1024**3))
        if used_bytes + need_bytes <= quota_bytes:
            return evicted

        candidates = []
        categories = [category] if category else list(self.source_dirs.keys())
        for cat in categories:
            candidates.extend(self._list_remote_objects(cat))
        candidates.sort(key=lambda o: o.get("modified_ts") or 0)

        uploads = self._uploads_map()
        for obj in candidates:
            if used_bytes + need_bytes <= quota_bytes:
                break
            cat = obj.get("category") or ""
            rel = obj.get("rel_path") or ""
            if not cat or not rel:
                continue
            if self.remote_backend.delete_object(cat, rel):
                evicted += 1
                used_bytes -= int(obj.get("size") or 0)
                uploads.pop(self._upload_key(cat, rel), None)

        return evicted

    def _purge_cloud_buffer(self) -> int:
        """
        Rolling N-day cloud buffer: delete remote objects older than remote_days.
        Uses bucket listing when available; also cleans tracked upload map.
        """
        if self.remote_backend is None or not getattr(self.remote_backend, "is_active", False):
            return 0
        now = time.time()
        purged = 0
        seen: set = set()
        uploads = self._uploads_map()

        for category in self.source_dirs:
            remote_days = self._remote_days(category)
            if remote_days <= 0:
                continue
            max_age = remote_days * 86400
            for obj in self._list_remote_objects(category):
                map_key = self._upload_key(obj["category"], obj["rel_path"])
                if map_key in seen:
                    continue
                meta = uploads.get(map_key, {})
                age_base = self._object_age_ts(meta) if meta else float(obj.get("modified_ts") or 0)
                if not age_base:
                    continue
                if (now - age_base) < max_age:
                    continue
                if self.remote_backend.delete_object(obj["category"], obj["rel_path"]):
                    purged += 1
                    seen.add(map_key)
                    uploads.pop(map_key, None)

        for map_key, meta in list(uploads.items()):
            if map_key in seen or ":" not in map_key:
                continue
            cat, rel_path = map_key.split(":", 1)
            remote_days = self._remote_days(cat)
            if remote_days <= 0:
                continue
            age_base = self._object_age_ts(meta)
            if not age_base or (now - age_base) < remote_days * 86400:
                continue
            if self.remote_backend.delete_object(cat, rel_path):
                purged += 1
                uploads.pop(map_key, None)

        return purged

    def _free_gb_for_path(self, path: str) -> float:
        try:
            probe = path if path and os.path.exists(path) else os.path.dirname(self._state_file)
            usage = shutil.disk_usage(probe or "/")
            return usage.free / (1024**3)
        except OSError:
            return 999.0

    def _collect_oldest_media(
        self, categories: Tuple[str, ...], limit: int = 250000
    ) -> List[Tuple[float, str, int, str]]:
        """Return (mtime, path, size, category) sorted oldest-first (capped)."""
        candidates: List[Tuple[float, str, int, str]] = []
        for category in categories:
            src_dir = self.source_dirs.get(category)
            if not src_dir or not os.path.isdir(src_dir):
                continue
            for root, dirs, files in os.walk(src_dir):
                self._hide_plant_dirs(dirs)
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in _ALL_MEDIA_EXTS:
                        continue
                    full = os.path.join(root, fname)
                    try:
                        st = os.stat(full)
                        candidates.append((st.st_mtime, full, int(st.st_size), category))
                    except OSError:
                        continue
                    if len(candidates) >= limit:
                        candidates.sort(key=lambda x: x[0])
                        return candidates
        candidates.sort(key=lambda x: x[0])
        return candidates

    def _disk_free_min_percent(self) -> float:
        try:
            from basebuddy.modules import config as cfg

            return max(0.0, min(50.0, float(getattr(cfg, "DISK_FREE_MIN_PERCENT", 5.0))))
        except Exception:
            return 5.0

    def _floor_bytes(self, total_bytes: int) -> int:
        return int(max(self.disk_free_min_gb * (1024**3),
                       total_bytes * self._disk_free_min_percent() / 100.0))

    def _evict_for_disk_pressure(self) -> Dict[str, Any]:
        """
        For each physical drive holding detections/stills, when free space is below
        max(disk_free_min_gb, DISK_FREE_MIN_PERCENT of that drive), delete the oldest
        files *on that drive* until free space is 10 GiB above the floor.
        """
        if self.disk_free_min_gb <= 0 and self._disk_free_min_percent() <= 0:
            return {"skipped": True, "reason": "disabled"}

        by_dev: Dict[int, List[str]] = {}
        for c in _DISK_PRESSURE_CATEGORIES:
            d = self.source_dirs.get(c)
            if not d or not os.path.isdir(d):
                continue
            try:
                by_dev.setdefault(os.stat(d).st_dev, []).append(c)
            except OSError:
                continue
        if not by_dev:
            return {"skipped": True, "reason": "no_dirs"}

        result: Dict[str, Any] = {"devices": []}
        total_deleted, total_freed = 0, 0
        by_cat_all: Dict[str, int] = {}
        for dev, cats in by_dev.items():
            probe = self.source_dirs[cats[0]]
            try:
                usage = shutil.disk_usage(probe)
            except OSError:
                continue
            floor = self._floor_bytes(usage.total)
            entry = {"probe": probe, "categories": cats,
                     "free_gb": round(usage.free / (1024**3), 2),
                     "floor_gb": round(floor / (1024**3), 2)}
            if usage.free >= floor:
                entry["skipped"] = "ok"
                result["devices"].append(entry)
                continue
            need_bytes = floor + 10 * (1024**3) - usage.free
            logger.warning(
                "Disk pressure on %s: %.1f GiB free (floor %.1f GiB) — deleting oldest %s",
                probe, usage.free / (1024**3), floor / (1024**3), "/".join(cats),
            )
            deleted = freed = 0
            for _mtime, path, size, category in self._collect_oldest_media(tuple(cats)):
                if freed >= need_bytes:
                    break
                try:
                    os.remove(path)
                    deleted += 1
                    freed += size
                    by_cat_all[category] = by_cat_all.get(category, 0) + 1
                except OSError as exc:
                    logger.error("Disk-pressure delete failed %s: %s", path, exc)
            after = self._free_gb_for_path(probe)
            entry.update(deleted=deleted, freed_mb=round(freed / (1024 * 1024), 2), free_gb_after=round(after, 2))
            logger.warning("Disk pressure freed %.1f GiB (%d files) on %s. Free now %.1f GiB",
                           freed / (1024**3), deleted, probe, after)
            result["devices"].append(entry)
            total_deleted += deleted
            total_freed += freed

        if not total_deleted:
            result.update(skipped=True, reason="ok")
            if result["devices"]:
                result["free_gb"] = min(d["free_gb"] for d in result["devices"])
            return result
        result.update(deleted=total_deleted, freed_mb=round(total_freed / (1024 * 1024), 2),
                      by_category=by_cat_all)
        return result

    def _analytics_db_check(self) -> Dict[str, Any]:
        """Track analytics.db size; warn when it is large or would fill its disk soon."""
        try:
            from basebuddy.core.paths import get_repo_root
            from basebuddy.modules import config as cfg

            path = getattr(cfg, "ANALYTICS_DB_PATH", None) or os.path.join(get_repo_root(), "analytics.db")
            warn_gb = float(getattr(cfg, "ANALYTICS_DB_WARN_GB", 10.0))
            if not os.path.isfile(path):
                return {"skipped": True}
            size = os.path.getsize(path)
            usage = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
        except Exception as exc:
            return {"error": str(exc)}
        now = time.time()
        hist = self._state.setdefault("analytics_db_history", [])
        if not hist or now - hist[-1][0] >= 6 * 3600:
            hist.append([now, size])
            del hist[:-60]
        growth_per_day = 0.0
        if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
            growth_per_day = max(0.0, (hist[-1][1] - hist[0][1]) / ((hist[-1][0] - hist[0][0]) / 86400))
        days_left = (usage.free / growth_per_day) if growth_per_day > 0 else None
        warnings = []
        if size > warn_gb * (1024**3):
            warnings.append(f"analytics.db is {size / (1024**3):.1f} GiB (> {warn_gb:.0f} GiB)")
        if days_left is not None and days_left < 60:
            warnings.append(f"analytics.db growth {growth_per_day / (1024**3):.2f} GiB/day would fill its disk in {days_left:.0f} days")
        info = {"size_gb": round(size / (1024**3), 2),
                "growth_gb_per_day": round(growth_per_day / (1024**3), 3),
                "disk_free_gb": round(usage.free / (1024**3), 2),
                "warnings": warnings}
        if warnings:
            logger.warning("Storage warning: %s", "; ".join(warnings))
        return info

    def perform_retention_pass(self) -> dict:
        if not self.enabled:
            return {"skipped": True, "reason": "disabled"}

        deleted = 0
        uploaded = 0
        freed = 0
        upload_failures = 0

        # Rolling cloud buffer first — makes room before new uploads
        remote_purged = self._purge_cloud_buffer()

        scans: List[Tuple[str, str]] = list(self.source_dirs.items())
        plant_days = int((self.retention_policy.get("plants") or {}).get("local_days") or 0)
        if plant_days > 0:
            seen = set()
            for key in ("stills", "detections"):
                root = self.source_dirs.get(key)
                if root and root not in seen:
                    seen.add(root)
                    scans.append(("plants", root))

        for category, src_dir in scans:
            cfg = self.retention_policy.get(category) or {}
            local_days = int(cfg.get("local_days") or 0)
            if local_days <= 0:
                continue
            max_age = local_days * 86400
            for local_path, rel_path, mtime in list(self._iter_category_files(category, src_dir, max_age)):
                if self._should_upload(category):
                    ok, _rk = self._maybe_upload(category, local_path, rel_path, mtime)
                    if not ok:
                        upload_failures += 1
                        continue
                    uploaded += 1
                try:
                    size = os.path.getsize(local_path)
                    os.remove(local_path)
                    deleted += 1
                    freed += size
                except OSError as exc:
                    logger.error("Retention delete failed %s: %s", local_path, exc)

        pressure = self._evict_for_disk_pressure()
        self._state["analytics_db"] = self._analytics_db_check()
        pressure_deleted = int(pressure.get("deleted") or 0)
        if pressure_deleted:
            deleted += pressure_deleted
            freed += int(float(pressure.get("freed_mb") or 0) * 1024 * 1024)

        totals = self._state.setdefault("totals", {})
        totals["deleted"] = totals.get("deleted", 0) + deleted
        totals["uploaded"] = totals.get("uploaded", 0) + uploaded
        totals["remote_purged"] = totals.get("remote_purged", 0) + remote_purged
        totals["disk_pressure_deleted"] = totals.get("disk_pressure_deleted", 0) + pressure_deleted
        self._state["last_run"] = time.time()
        self._state["last_disk_pressure"] = pressure
        self._save_state()

        return {
            "deleted": deleted,
            "uploaded": uploaded,
            "upload_failures": upload_failures,
            "remote_purged": remote_purged,
            "freed_mb": round(freed / (1024 * 1024), 2),
            "remote_backend": self.remote_backend_kind,
            "disk_pressure": pressure,
        }

    def _loop(self) -> None:
        while self.running:
            try:
                if self.enabled:
                    summary = self.perform_retention_pass()
                    if (
                        summary.get("deleted")
                        or summary.get("uploaded")
                        or (summary.get("disk_pressure") or {}).get("deleted")
                    ):
                        logger.info("Retention pass: %s", summary)
                time.sleep(self.scan_interval_seconds)
            except Exception as exc:
                logger.error("Retention loop error: %s", exc)
                time.sleep(60)

    def start(self) -> None:
        if not self.enabled:
            logger.info("Retention service disabled")
            return
        if self.running:
            return
        logger.info("Starting retention service (interval=%ds)", self.scan_interval_seconds)
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_status(self) -> dict:
        last = self._state.get("last_run", 0)
        return {
            "enabled": self.enabled,
            "running": self.running,
            "last_run": last,
            "next_run": last + self.scan_interval_seconds if last else None,
            "remote_backend": self.remote_backend_kind,
            "remote_active": bool(
                self.remote_backend and getattr(self.remote_backend, "is_active", False)
            ),
            "disk_free_min_gb": self.disk_free_min_gb,
            "last_disk_pressure": self._state.get("last_disk_pressure"),
            "disk_free_min_percent": self._disk_free_min_percent(),
            "analytics_db": self._state.get("analytics_db"),
            "totals": self._state.get("totals", {}),
            "retention_policy": self.retention_policy,
        }
