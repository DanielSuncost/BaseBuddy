"""History reanalyze writes a new series without touching the old one."""
from __future__ import annotations

import sqlite3
import time

import cv2
import numpy as np
import pytest

from basebuddy.plugins.plant_health import db as plant_db
from basebuddy.plugins.plant_health import reanalyze as re_mod
from basebuddy.plugins.plant_health import sam_series as series_mod


class _MemDb:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")

    def _connect(self):
        return _ConnProxy(self._conn)


class _ConnProxy:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, *a, **k):
        return self._conn.execute(*a, **k)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def plant_mem(monkeypatch):
    db = _MemDb()
    monkeypatch.setattr(series_mod, "_db", lambda: db)
    monkeypatch.setattr(plant_db, "_db", lambda: db)
    monkeypatch.setattr(re_mod, "_archive_dirs", lambda camera_id: [])
    with db._connect() as conn:
        plant_db.init_plant_tables(conn)
    return db


def _write_still(path, *, w=160, h=90, plant_x=100):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (40, 50, 60)
    img[10:80, plant_x : plant_x + 40] = (40, 180, 40)
    cv2.imwrite(str(path), img)
    return str(path)


def test_reanalyze_creates_new_series_and_keeps_old(plant_mem, monkeypatch, tmp_path):
    old = series_mod.create_series(
        "m1", 0, [[20, 20]], [1], frame_w=80, frame_h=90, label="original"
    )
    t0 = 1_700_000_000.0
    still_a = _write_still(tmp_path / "a.jpg")
    still_b = _write_still(tmp_path / "b.jpg")
    series_mod.save_growth_sample(
        old["id"],
        "m1",
        {"mask_area_px": 111, "green_pixels_in_mask": 50, "quality_flags": []},
        raw_path=still_a,
        sampled_at=t0,
    )
    plant_db.save_color_sample(
        "m1",
        0,
        {
            "coverage": 0.1,
            "plant_pixels": 200,
            "hsv_mean": {"h": 40, "s": 80, "v": 80},
            "rgb_mean": {"r": 40, "g": 180, "b": 40},
            "greenness": 0.5,
            "G_rel": 0.5,
            "L_bg": 0.6,
            "G_norm": 0.8,
            "sample_ok": True,
        },
        image_path=still_b,
        sampled_at=t0 + 3600,
    )

    mask = np.zeros((90, 80), dtype=np.uint8)
    mask[10:80, 10:70] = 255
    mask_path = str(tmp_path / "mask.png")
    cv2.imwrite(mask_path, mask)

    monitor = {
        "id": "m1",
        "camera_id": 0,
        "frame_crop": {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0},
        "segmentation": {"mask_path": mask_path, "mode": "color_profile"},
        "sam_growth": {"green_refine": 0},
    }
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: monitor,
    )
    monkeypatch.setattr(re_mod, "_save_exemplar", lambda *a, **k: None)
    monkeypatch.setattr(
        re_mod,
        "_save_playback_stills",
        lambda *a, **k: {"mask_path": "m.jpg", "silhouette_path": "s.jpg", "raw_path": "r.jpg"},
    )
    monkeypatch.setattr(re_mod, "_refresh_mask_gif", lambda *a, **k: None)

    result = re_mod.reanalyze_history("m1")
    assert result["ok"] is True
    assert result["ok_count"] == 2
    assert result["series_id"] != old["id"]

    old_samples = series_mod.list_growth_samples(old["id"])
    assert len(old_samples) == 1
    assert old_samples[0]["mask_area_px"] == 111
    assert series_mod.get_series(old["id"])["active"] is False

    new_samples = series_mod.list_growth_samples(result["series_id"])
    assert len(new_samples) == 2
    assert [round(s["sampled_at"]) for s in new_samples] == [t0, t0 + 3600]
    assert all(s["mask_area_px"] and s["mask_area_px"] > 0 for s in new_samples)
    assert series_mod.get_active_series("m1")["id"] == result["series_id"]

    original_color = plant_db.list_color_timeline("m1", limit=20)
    assert len(original_color) == 1
    rebuilt = plant_db.list_color_timeline("m1", limit=20, series_id=result["series_id"])
    assert len(rebuilt) == 2
    datasets = plant_db.list_color_datasets("m1")
    assert len(datasets) == 2


def test_collect_source_frames_prefers_color_still(plant_mem, tmp_path):
    t0 = time.time()
    color = _write_still(tmp_path / "color.jpg")
    raw = _write_still(tmp_path / "raw.jpg")
    s = series_mod.create_series("m1", 0, [[1, 1]], [1])
    series_mod.save_growth_sample(
        s["id"], "m1", {"mask_area_px": 1, "quality_flags": []},
        raw_path=raw, sampled_at=t0,
    )
    plant_db.save_color_sample(
        "m1", 0, {"coverage": 0.1, "plant_pixels": 10, "greenness": 0.4},
        image_path=color, sampled_at=t0,
    )
    frames = re_mod.collect_source_frames("m1")
    assert len(frames) == 1
    assert frames[0]["path"] == color


def test_timestamp_from_camera_still_name():
    assert re_mod.timestamp_from_filename("20260921_135840.jpg") is not None
    assert re_mod.timestamp_from_filename("plant_1783420988.jpg") == 1783420988.0
    assert re_mod.timestamp_from_filename("readme.txt") is None


def test_collect_source_frames_includes_thinned_archive(plant_mem, monkeypatch, tmp_path):
    cam_dir = tmp_path / "camera_3"
    cam_dir.mkdir()
    # Three stills in the same hour — only one should survive thinning.
    for i, name in enumerate(("20260101_120000.jpg", "20260101_121500.jpg", "20260101_140000.jpg")):
        _write_still(cam_dir / name)
    monkeypatch.setattr(re_mod, "_archive_dirs", lambda camera_id: [str(cam_dir)])
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: {"id": mid, "camera_id": 3},
    )
    frames = re_mod.collect_source_frames("m1", camera_id=3, interval_s=3600)
    kinds = {f["kind"] for f in frames}
    assert "camera_still" in kinds or "archive" in kinds or frames
    hours = {int(f["sampled_at"] // 3600) for f in frames}
    assert len(hours) == 2
    assert len(frames) == 2
