"""Tests for the adaptive external-drive storage budget and backup pruning."""
import errno
import os
import time

import pytest

from basebuddy.core import storage_budget as sb
from basebuddy.core.storage_budget import BudgetSettings, DiskInfo, compute_budget, round_up

GIB = sb.GIB


def test_round_up_to_cluster():
    assert round_up(0) == 0
    assert round_up(1) == 128 * 1024
    assert round_up(128 * 1024) == 128 * 1024
    assert round_up(128 * 1024 + 1) == 256 * 1024


def test_budget_limited_by_percent_when_drive_empty():
    disk = DiskInfo(total=1000 * GIB, free=900 * GIB, cluster=131072)
    b = compute_budget(disk, own_bytes=100 * GIB, settings=BudgetSettings(50, 20, 5))
    assert b["other_bytes"] == 0
    assert b["floor_bytes"] == 50 * GIB  # 5% > 20 GiB
    assert b["budget_bytes"] == 500 * GIB
    assert not b["crowded_out"]


def test_budget_crowded_out_by_other_data():
    # Mirrors the real HRL drive: ~1863 GiB, 0 free, BaseBuddy 679 GiB.
    total = int(1862.6 * GIB)
    disk = DiskInfo(total=total, free=0, cluster=131072)
    b = compute_budget(disk, own_bytes=679 * GIB, settings=BudgetSettings(50, 20, 5))
    assert b["crowded_out"]
    expected = total - (total - 679 * GIB) - int(total * 0.05)
    assert b["budget_bytes"] == expected
    assert b["over_budget_bytes"] == 679 * GIB - expected
    assert b["free_shortfall_bytes"] == b["floor_bytes"]


def test_budget_follows_drive_size_change():
    s = BudgetSettings(50, 20, 5)
    small = compute_budget(DiskInfo(500 * GIB, 400 * GIB, 4096), 50 * GIB, s)
    big = compute_budget(DiskInfo(4000 * GIB, 3900 * GIB, 4096), 50 * GIB, s)
    assert small["budget_bytes"] == 250 * GIB
    assert big["budget_bytes"] == 2000 * GIB


def test_budget_never_negative():
    disk = DiskInfo(total=100 * GIB, free=0, cluster=4096)
    b = compute_budget(disk, own_bytes=0, settings=BudgetSettings(50, 20, 5))
    assert b["budget_bytes"] == 0


def test_settings_normalized_clamps():
    s = BudgetSettings(max_percent=500, min_free_gb=-3, min_free_percent=99).normalized()
    assert s.max_percent == 95.0 and s.min_free_gb == 0.0 and s.min_free_percent == 50.0


def _make_files(root, n, size=1000, start=1_600_000_000):
    paths = []
    for i in range(n):
        d = os.path.join(root, "cam1", f"day{i}")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"f{i}.mp4")
        with open(p, "wb") as fh:
            fh.write(b"x" * size)
        os.utime(p, (start + i * 3600, start + i * 3600))
        paths.append(p)
    return paths


def _budget(tmp_path, monkeypatch, total, free, n=10, cluster=131072):
    root = tmp_path / "drive" / "backup_surveillance_recordings"
    root.mkdir(parents=True)
    paths = _make_files(str(root), n)
    monkeypatch.setattr(sb, "read_disk", lambda p: DiskInfo(total, free, cluster))
    db = sb.DriveBudget(str(tmp_path / "drive"))
    m = db.register(str(root), str(tmp_path / "manifest.json"))
    m.ensure_built(background=False)
    return db, m, root, paths


def test_manifest_counts_cluster_rounded_usage(tmp_path, monkeypatch):
    db, m, root, paths = _budget(tmp_path, monkeypatch, 100 * GIB, 50 * GIB, n=4)
    assert m.count() == 4
    assert m.total() == 4 * 131072
    # persisted and reloadable
    m2 = sb.UsageManifest(str(root), str(tmp_path / "manifest.json"), cluster=131072)
    assert m2.ready and m2.total() == m.total()


def test_dry_run_deletes_nothing_and_reports_oldest(tmp_path, monkeypatch):
    cl = 131072
    # floor = max(0, 10% of 100 clusters) = 10 clusters; free = 7 clusters -> need 3 clusters
    db, m, root, paths = _budget(tmp_path, monkeypatch, total=100 * cl, free=7 * cl)
    s = BudgetSettings(max_percent=95, min_free_gb=0, min_free_percent=10, dry_run=True)
    plan = db.prune(s, need_bytes=0)
    assert plan["dry_run"] is True
    assert plan["deleted_files"] == 3
    assert all(os.path.exists(p) for p in paths)
    assert plan["sample"][0].endswith("f0.mp4")
    assert plan["oldest_mtime"] < plan["newest_mtime"]
    assert m.count() == 10


def test_real_prune_oldest_first_only_inside_root(tmp_path, monkeypatch):
    cl = 131072
    outsider = tmp_path / "drive" / "someone_elses_file.mp4"
    db, m, root, paths = _budget(tmp_path, monkeypatch, total=100 * cl, free=7 * cl)
    outsider.write_bytes(b"y" * 5000)
    os.utime(outsider, (1, 1))  # older than everything
    s = BudgetSettings(max_percent=95, min_free_gb=0, min_free_percent=10, dry_run=False)
    plan = db.prune(s, need_bytes=0)
    assert plan["deleted_files"] == 3
    assert [os.path.exists(p) for p in paths[:4]] == [False, False, False, True]
    assert outsider.exists()
    # emptied day dirs removed, root kept
    assert not os.path.isdir(os.path.dirname(paths[0]))
    assert root.is_dir()
    assert m.count() == 7
    assert db.bytes_pruned_total == 3 * cl


def test_prune_refuses_manifest_entry_escaping_root(tmp_path, monkeypatch):
    cl = 131072
    db, m, root, paths = _budget(tmp_path, monkeypatch, total=100 * cl, free=0, n=1)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    m.add("../../victim.txt", 10, mtime=1)
    s = BudgetSettings(max_percent=95, min_free_gb=0, min_free_percent=10, dry_run=False)
    plan = db.prune(s)
    assert victim.exists()
    assert plan["failed"] >= 1


def test_prune_within_budget_is_noop(tmp_path, monkeypatch):
    db, m, root, paths = _budget(tmp_path, monkeypatch, total=1000 * GIB, free=900 * GIB)
    plan = db.prune(BudgetSettings(50, 20, 5, dry_run=False))
    assert plan.get("skipped") and plan["reason"] == "within_budget"
    assert all(os.path.exists(p) for p in paths)


def test_drive_state_missing_and_full_and_readonly(tmp_path, monkeypatch):
    assert sb.drive_state(str(tmp_path / "nope"), str(tmp_path / "nope" / "b"))[0] == "missing"
    drive = tmp_path / "d"
    drive.mkdir()

    class FakeVfs:
        f_bavail = 0; f_frsize = 4096; f_bsize = 131072; f_blocks = 1000; f_flag = 0
    monkeypatch.setattr(sb.os, "statvfs", lambda p: FakeVfs)
    assert sb.drive_state(str(drive), str(drive / "b"))[0] == "full"
    FakeVfs.f_flag = getattr(os, "ST_RDONLY", 1)
    assert sb.drive_state(str(drive), str(drive / "b"))[0] == "read_only"


def test_backup_enospc_prunes_and_retries_once(tmp_path, monkeypatch):
    from basebuddy.core.services.backup_service import BackupManager

    rec = tmp_path / "recordings" / "cam1"
    rec.mkdir(parents=True)
    src = rec / "a.mp4"
    src.write_bytes(b"z" * 2048)
    old = time.time() - 3600
    os.utime(src, (old, old))
    drive = tmp_path / "drive"
    drive.mkdir()
    bm = BackupManager(str(tmp_path / "recordings"), str(drive), "backup",
                       budget_settings=BudgetSettings(dry_run=True))
    calls = []
    monkeypatch.setattr(bm, "_ensure_space", lambda need, factor=1.0: calls.append((need, factor)) or {})
    real_copy = bm._copy
    state = {"n": 0}

    def flaky(s, d):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy(s, d)

    monkeypatch.setattr(bm, "_copy", flaky)
    bm.perform_backup()
    st = bm.get_status()
    assert state["n"] == 2
    assert calls[-1][1] == 2.0  # emergency prune with headroom
    assert st["last_run"]["ok"] == 1 and st["last_run"]["failed"] == 0
    assert st["consecutive_failures"] == 0
    assert st["last_success"]


def test_backup_counts_failures_correctly(tmp_path, monkeypatch):
    from basebuddy.core.services.backup_service import BackupManager

    rec = tmp_path / "recordings"
    (rec / "c").mkdir(parents=True)
    for i in range(3):
        f = rec / "c" / f"{i}.mp4"
        f.write_bytes(b"q")
        os.utime(f, (time.time() - 3600,) * 2)
    drive = tmp_path / "drive"
    drive.mkdir()
    bm = BackupManager(str(rec), str(drive), "backup", budget_settings=BudgetSettings(dry_run=True))
    monkeypatch.setattr(bm, "_ensure_space", lambda need, factor=1.0: {})

    def always_full(s, d):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(bm, "_copy", always_full)
    bm.perform_backup()
    st = bm.get_status()
    assert st["last_run"]["ok"] == 0 and st["last_run"]["failed"] == 3
    assert st["consecutive_failures"] == 1
    assert "3 of 3 files failed" in st["last_error"]
    h = bm.storage_health()
    assert h["level"] in ("warning", "critical")


def test_missing_drive_is_skipped_and_reported(tmp_path):
    from basebuddy.core.services.backup_service import BackupManager

    bm = BackupManager(str(tmp_path / "rec"), str(tmp_path / "nodrive"), "backup",
                       budget_settings=BudgetSettings())
    bm.perform_backup()
    st = bm.get_status()
    assert st["drive_state"] == "missing"
    assert st["consecutive_failures"] == 1
    assert bm.storage_health()["level"] == "critical"


def test_settings_caps_sizes_to_drive_capacity(tmp_path, monkeypatch):
    from basebuddy.pages.storage_policy import api

    monkeypatch.setattr(api, "_capacity_gib", lambda p: 100.0)
    out = api._coerce_settings_body({
        "BACKUP_DRIVE_PATH": str(tmp_path),
        "BACKUP_MIN_FREE_GB": 5000,
        "DISK_FREE_MIN_GB": 5000,
        "STORAGE_QUOTA_GB": 5000,
        "BACKUP_MAX_PERCENT": 400,
        "BACKUP_PRUNE_DRY_RUN": False,
    })
    assert float(out["BACKUP_MIN_FREE_GB"]) == 50.0
    assert float(out["DISK_FREE_MIN_GB"]) == 50.0
    assert float(out["STORAGE_QUOTA_GB"]) == 90.0
    assert float(out["BACKUP_MAX_PERCENT"]) == 95.0
    assert out["BACKUP_PRUNE_DRY_RUN"] == "false"


def test_health_storage_block_shape(monkeypatch):
    import basebuddy.modules.state as shared_state
    from basebuddy.core.api.health import storage_health_block

    class FakeBM:
        def storage_health(self):
            return {"level": "critical", "reason": "backup drive full", "free_gb": 0.0,
                    "last_success": 123.0, "crowded_out": True}

    monkeypatch.setattr(shared_state, "backup_manager", FakeBM(), raising=False)
    monkeypatch.setattr(shared_state, "retention_service", None, raising=False)
    b = storage_health_block()
    assert b["level"] == "critical"
    assert "full" in b["reason"]
    assert b["crowded_out"] is True and b["free_gb"] == 0.0 and b["last_success"] == 123.0


def test_disk_pressure_uses_percent_floor_per_drive(tmp_path, monkeypatch):
    from collections import namedtuple
    from basebuddy.core.services import retention_service as rsm

    det = tmp_path / "media"
    det.mkdir()
    for i in range(5):
        f = det / f"d{i}.jpg"
        f.write_bytes(b"j" * 1024)
        os.utime(f, (1_600_000_000 + i,) * 2)
    Usage = namedtuple("Usage", "total used free")
    # 1 TiB drive, 40 GiB free: above the 20 GiB fixed floor but below 5% (51 GiB)
    monkeypatch.setattr(rsm.shutil, "disk_usage", lambda p: Usage(1024 * GIB, 984 * GIB, 40 * GIB))
    svc = rsm.RetentionService({"detections": str(det)}, {}, disk_free_min_gb=20.0)
    monkeypatch.setattr(svc, "_save_state", lambda: None)
    res = svc._evict_for_disk_pressure()
    assert res.get("deleted") == 5  # tiny files can't reach target, so all are removed oldest-first
    assert res["devices"][0]["floor_gb"] == pytest.approx(51.2, rel=1e-3)
