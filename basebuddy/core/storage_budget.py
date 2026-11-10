"""
Adaptive storage budget for BaseBuddy data on an external (often shared) drive.

Every cycle the drive's real capacity and free space are re-read, so the budget
follows the drive even if it is swapped for a bigger/smaller one or other data
on it grows or shrinks.

    floor  = max(min_free_gb GiB, min_free_percent % of capacity)
    budget = max(0, min(max_percent % of capacity,
                        capacity - other_usage - floor))
    other_usage = (capacity - free) - basebuddy_usage

BaseBuddy usage is tracked by an incremental on-disk manifest (one per managed
root) so we do not rescan ~100k files on a slow exFAT drive every cycle.  The
manifest is built lazily on a background thread the first time it is needed and
re-reconciled periodically.

Pruning only ever deletes files inside registered roots (path-checked), oldest
first, and removes the directories it empties.  A dry-run mode reports exactly
what would be deleted without touching anything.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("basebuddy")

GIB = 1024 ** 3
DEFAULT_CLUSTER = 128 * 1024  # exFAT default for 2 TB drives
RECONCILE_EVERY_S = 24 * 3600


@dataclass
class BudgetSettings:
    max_percent: float = 50.0       # never use more than this share of the drive
    min_free_gb: float = 20.0       # always leave at least this much free (GiB) ...
    min_free_percent: float = 5.0   # ... or this share of capacity, whichever is larger
    dry_run: bool = True            # log what would be pruned, delete nothing

    def normalized(self) -> "BudgetSettings":
        def _f(v, lo, hi, default):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return default
            return min(hi, max(lo, v))
        return BudgetSettings(
            max_percent=_f(self.max_percent, 1.0, 95.0, 50.0),
            min_free_gb=_f(self.min_free_gb, 0.0, 1e9, 20.0),
            min_free_percent=_f(self.min_free_percent, 0.0, 50.0, 5.0),
            dry_run=bool(self.dry_run),
        )


def settings_from_config(cfg=None) -> BudgetSettings:
    if cfg is None:
        from basebuddy.modules import config as cfg  # type: ignore
    return BudgetSettings(
        max_percent=getattr(cfg, "BACKUP_MAX_PERCENT", 50.0),
        min_free_gb=getattr(cfg, "BACKUP_MIN_FREE_GB", 20.0),
        min_free_percent=getattr(cfg, "BACKUP_MIN_FREE_PERCENT", 5.0),
        dry_run=getattr(cfg, "BACKUP_PRUNE_DRY_RUN", True),
    ).normalized()


def round_up(n: int, cluster: int = DEFAULT_CLUSTER) -> int:
    n = max(0, int(n))
    cluster = max(1, int(cluster))
    return ((n + cluster - 1) // cluster) * cluster if n else 0


@dataclass
class DiskInfo:
    total: int
    free: int
    cluster: int

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)


def read_disk(path: str) -> DiskInfo:
    """Re-read capacity / free space / allocation unit for *path* (no caching)."""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    cluster = max(st.f_bsize or 0, st.f_frsize or 0, 4096)
    return DiskInfo(total=int(total), free=int(free), cluster=int(cluster))


def compute_budget(disk: DiskInfo, own_bytes: int, settings: BudgetSettings) -> Dict:
    s = settings.normalized()
    total = disk.total
    other = max(0, disk.used - int(own_bytes))
    floor = int(max(s.min_free_gb * GIB, total * s.min_free_percent / 100.0))
    cap_percent = int(total * s.max_percent / 100.0)
    cap_space = int(total - other - floor)
    budget = max(0, min(cap_percent, cap_space))
    crowded_out = cap_space < cap_percent
    return {
        "total_bytes": total,
        "free_bytes": disk.free,
        "own_bytes": int(own_bytes),
        "other_bytes": other,
        "floor_bytes": floor,
        "budget_bytes": budget,
        "budget_percent_cap_bytes": cap_percent,
        "over_budget_bytes": max(0, int(own_bytes) - budget),
        "free_shortfall_bytes": max(0, floor - disk.free),
        "crowded_out": bool(crowded_out),
        "cluster_bytes": disk.cluster,
        "settings": asdict(s),
    }


def _is_within(root: str, path: str) -> bool:
    try:
        r = os.path.realpath(root)
        p = os.path.realpath(path)
    except OSError:
        return False
    return p != r and p.startswith(r.rstrip(os.sep) + os.sep)


class UsageManifest:
    """Incremental record of files under *root*: rel_path -> [alloc_bytes, mtime]."""

    def __init__(self, root: str, manifest_path: str, cluster: int = DEFAULT_CLUSTER):
        self.root = root
        self.manifest_path = manifest_path
        self.cluster = cluster
        self._lock = threading.RLock()
        self._entries: Dict[str, List[float]] = {}
        self._total = 0
        self.ready = False
        self.built_at = 0.0
        self.building = False
        self.build_error: Optional[str] = None
        self._dirty = 0
        self._thread: Optional[threading.Thread] = None
        self._load()

    # -- persistence --
    def _load(self) -> None:
        try:
            if os.path.isfile(self.manifest_path):
                with open(self.manifest_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("root") == self.root:
                    self._entries = {k: list(v) for k, v in (data.get("entries") or {}).items()}
                    self._total = sum(int(v[0]) for v in self._entries.values())
                    self.built_at = float(data.get("built_at") or 0)
                    self.ready = bool(data.get("complete"))
        except Exception as exc:
            logger.error("Storage manifest load failed (%s): %s", self.manifest_path, exc)

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not force and not self._dirty:
                return
            data = {
                "root": self.root,
                "complete": self.ready,
                "built_at": self.built_at,
                "entries": self._entries,
            }
            tmp = self.manifest_path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, separators=(",", ":"))
                os.replace(tmp, self.manifest_path)
                self._dirty = 0
            except Exception as exc:
                logger.error("Storage manifest save failed (%s): %s", self.manifest_path, exc)

    # -- building --
    def scan(self) -> None:
        """Full (blocking) rescan of root.  Safe to call from a background thread."""
        entries: Dict[str, List[float]] = {}
        total = 0
        if os.path.isdir(self.root):
            stack = [self.root]
            while stack:
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    stack.append(e.path)
                                elif e.is_file(follow_symlinks=False):
                                    st = e.stat(follow_symlinks=False)
                                    alloc = round_up(st.st_size, self.cluster)
                                    entries[os.path.relpath(e.path, self.root)] = [alloc, st.st_mtime]
                                    total += alloc
                            except OSError:
                                continue
                except OSError:
                    continue
        with self._lock:
            self._entries = entries
            self._total = total
            self.ready = True
            self.built_at = time.time()
            self._dirty += 1
        self.save(force=True)

    def ensure_built(self, background: bool = True) -> bool:
        """Start a background build/reconcile if needed. Returns True when usable now."""
        stale = self.ready and (time.time() - self.built_at) > RECONCILE_EVERY_S
        if self.ready and not stale:
            return True
        if not self.building:
            if not background:
                self._run_build()
                return self.ready
            self.building = True
            self._thread = threading.Thread(target=self._run_build, daemon=True,
                                            name="storage-manifest")
            self._thread.start()
        return self.ready  # a stale-but-complete manifest stays usable while reconciling

    def _run_build(self) -> None:
        self.building = True
        t0 = time.time()
        try:
            self.scan()
            self.build_error = None
            logger.info("Storage manifest built for %s: %d files, %.1f GiB (%.0fs)",
                        self.root, len(self._entries), self._total / GIB, time.time() - t0)
        except Exception as exc:
            self.build_error = str(exc)
            logger.error("Storage manifest build failed for %s: %s", self.root, exc)
        finally:
            self.building = False

    # -- incremental updates --
    def add(self, rel: str, size: int, mtime: Optional[float] = None) -> None:
        alloc = round_up(size, self.cluster)
        with self._lock:
            old = self._entries.get(rel)
            if old:
                self._total -= int(old[0])
            self._entries[rel] = [alloc, mtime if mtime is not None else time.time()]
            self._total += alloc
            self._dirty += 1

    def remove(self, rel: str) -> None:
        with self._lock:
            old = self._entries.pop(rel, None)
            if old:
                self._total -= int(old[0])
                self._dirty += 1

    def total(self) -> int:
        with self._lock:
            return int(self._total)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def oldest_first(self) -> List[Tuple[float, str, int]]:
        with self._lock:
            items = [(float(v[1]), k, int(v[0])) for k, v in self._entries.items()]
        items.sort()
        return items


class DriveBudget:
    """All BaseBuddy-managed roots on one drive share one budget."""

    def __init__(self, drive_path: str):
        self.drive_path = drive_path
        self.roots: Dict[str, UsageManifest] = {}
        self.lock = threading.RLock()
        self.last_budget: Optional[Dict] = None
        self.bytes_pruned_total = 0
        self.files_pruned_total = 0
        self.last_prune: Optional[Dict] = None
        self._last_log_key = None
        self._last_log_at = 0.0

    def register(self, root: str, manifest_path: str) -> UsageManifest:
        with self.lock:
            m = self.roots.get(root)
            if m is None:
                cluster = DEFAULT_CLUSTER
                try:
                    cluster = read_disk(self.drive_path).cluster
                except OSError:
                    pass
                m = UsageManifest(root, manifest_path, cluster=cluster)
                self.roots[root] = m
            return m

    def ready(self) -> bool:
        return all(m.ensure_built() for m in self.roots.values()) if self.roots else False

    def own_bytes(self) -> int:
        return sum(m.total() for m in self.roots.values())

    def budget(self, settings: BudgetSettings) -> Dict:
        disk = read_disk(self.drive_path)
        b = compute_budget(disk, self.own_bytes(), settings)
        b["manifest_ready"] = self.ready()
        self.last_budget = b
        return b

    def prune(self, settings: BudgetSettings, need_bytes: int = 0,
              dry_run: Optional[bool] = None) -> Dict:
        """Delete (or, in dry-run, list) oldest files until under budget with room for need_bytes."""
        s = settings.normalized()
        dry = s.dry_run if dry_run is None else bool(dry_run)
        with self.lock:
            if not self.ready():
                return {"skipped": True, "reason": "manifest_building", "dry_run": dry}
            b = self.budget(s)
            need = round_up(need_bytes, b["cluster_bytes"])
            own = b["own_bytes"]
            free = b["free_bytes"]
            to_free_budget = max(0, own + need - b["budget_bytes"])
            to_free_floor = max(0, b["floor_bytes"] + need - free)
            target = max(to_free_budget, to_free_floor)
            plan = {
                "dry_run": dry, "target_bytes": target, "need_bytes": need,
                "deleted_files": 0, "deleted_bytes": 0, "failed": 0,
                "oldest_mtime": None, "newest_mtime": None,
                "free_before_bytes": free, "budget_bytes": b["budget_bytes"],
                "own_before_bytes": own, "sample": [],
            }
            if target <= 0:
                plan["skipped"] = True
                plan["reason"] = "within_budget"
                return plan
            candidates: List[Tuple[float, str, int, UsageManifest, str]] = []
            for root, m in self.roots.items():
                for mtime, rel, alloc in m.oldest_first():
                    candidates.append((mtime, os.path.join(root, rel), alloc, m, rel))
            candidates.sort(key=lambda c: c[0])
            freed = 0
            touched_dirs = set()
            for mtime, path, alloc, m, rel in candidates:
                if freed >= target:
                    break
                if not _is_within(m.root, path):
                    logger.error("Prune refused (outside managed root): %s", path)
                    plan["failed"] += 1
                    continue
                if dry:
                    ok = True
                else:
                    try:
                        if os.path.islink(path) or not os.path.isfile(path):
                            m.remove(rel)  # stale entry
                            continue
                        os.remove(path)
                        ok = True
                    except FileNotFoundError:
                        m.remove(rel)
                        continue
                    except OSError as exc:
                        logger.error("Prune delete failed %s: %s", path, exc)
                        plan["failed"] += 1
                        continue
                if ok:
                    if not dry:
                        m.remove(rel)
                        touched_dirs.add((m.root, os.path.dirname(path)))
                    freed += alloc
                    plan["deleted_files"] += 1
                    if plan["oldest_mtime"] is None:
                        plan["oldest_mtime"] = mtime
                    plan["newest_mtime"] = mtime
                    if len(plan["sample"]) < 5:
                        plan["sample"].append(rel)
            plan["deleted_bytes"] = freed
            plan["free_after_bytes"] = free + freed
            plan["own_after_bytes"] = own - freed
            if not dry:
                for root, d in touched_dirs:
                    _remove_empty_dirs_up(root, d)
                for m in self.roots.values():
                    m.save()
                self.bytes_pruned_total += freed
                self.files_pruned_total += plan["deleted_files"]
            self.last_prune = dict(plan, at=time.time())
            key = (dry, plan["deleted_files"] // 50, round(freed / GIB))
            if dry and key == self._last_log_key and time.time() - self._last_log_at < 3600:
                return plan  # throttle repeated identical dry-run reports
            self._last_log_key, self._last_log_at = key, time.time()
            logger.warning(
                "Storage prune %s: %d files, %.2f GiB (%s .. %s); free %.1f -> %.1f GiB; budget %.1f GiB",
                "DRY-RUN would delete" if dry else "deleted",
                plan["deleted_files"], freed / GIB,
                _fmt_ts(plan["oldest_mtime"]), _fmt_ts(plan["newest_mtime"]),
                free / GIB, (free + freed) / GIB, b["budget_bytes"] / GIB,
            )
            return plan


def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _remove_empty_dirs_up(root: str, d: str) -> None:
    root_r = os.path.realpath(root)
    cur = d
    while _is_within(root_r, cur):
        try:
            if os.listdir(cur):
                break
            os.rmdir(cur)
        except OSError:
            break
        cur = os.path.dirname(cur)


_registry: Dict[str, DriveBudget] = {}
_registry_lock = threading.Lock()


def drive_budget_for(path: str) -> DriveBudget:
    """One DriveBudget per physical device (st_dev), so backup + archive share it."""
    key = path
    try:
        key = f"dev:{os.stat(path).st_dev}"
    except OSError:
        pass
    with _registry_lock:
        db = _registry.get(key)
        if db is None:
            db = DriveBudget(path)
            _registry[key] = db
        return db


def drive_state(drive_path: str, target_dir: str, need_bytes: int = 0) -> Tuple[str, str]:
    """Classify the drive: ok | missing | read_only | full.  Never writes when missing."""
    if not drive_path or not os.path.isdir(drive_path):
        return "missing", f"drive path not found: {drive_path}"
    if drive_path.startswith(("/media/", "/mnt/", "/run/media/")) and not os.path.ismount(drive_path):
        return "missing", f"drive not mounted at {drive_path}"
    try:
        st = os.statvfs(drive_path)
        if st.f_flag & getattr(os, "ST_RDONLY", 1):
            return "read_only", "drive is mounted read-only"
        free = st.f_bavail * st.f_frsize
        cluster = max(st.f_bsize, st.f_frsize, 4096)
        if free < max(cluster, round_up(need_bytes, cluster)):
            return "full", f"drive full ({free} bytes free)"
    except OSError as exc:
        return "missing", f"cannot stat drive: {exc}"
    try:
        os.makedirs(target_dir, exist_ok=True)
        if not os.access(target_dir, os.W_OK):
            return "read_only", "backup folder not writable"
    except OSError as exc:
        import errno as _e
        if exc.errno == _e.ENOSPC:
            return "full", "drive full (no space to create folder)"
        if exc.errno == _e.EROFS:
            return "read_only", "drive is read-only"
        return "missing", f"cannot create backup folder: {exc}"
    return "ok", ""
