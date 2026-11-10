"""Tests for SAM growth proxies and series versioning (no real SAM/GPU)."""
from __future__ import annotations

import sqlite3
import time

import cv2
import numpy as np
import pytest

from basebuddy.plugins.plant_health.sam_growth import (
    count_green_in_mask,
    scale_points_to_frame,
)
from basebuddy.plugins.plant_health import sam_series as series_mod
from basebuddy.plugins.plant_health.sam_series import sample_window


def _green_frame(h=60, w=80):
    """BGR: green left half, brownish right half inside a full mask."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 2] = (40, 180, 40)  # green
    img[:, w // 2 :] = (40, 90, 180)  # orange/brown (BGR)
    mask = np.ones((h, w), dtype=np.uint8) * 255
    return img, mask


def test_count_green_in_mask_area_and_green():
    img, mask = _green_frame()
    m = count_green_in_mask(img, mask)
    assert m["mask_area_px"] == 60 * 80
    assert m["total_mask_pixels"] == m["mask_area_px"]
    assert m["mask_area_fraction"] == pytest.approx(1.0)
    # Left half green → roughly half the mask
    assert m["green_pixels_in_mask"] > 0.4 * m["mask_area_px"]
    assert m["green_pixels_in_mask"] < 0.6 * m["mask_area_px"]
    assert 0.4 < m["green_fraction_in_mask"] < 0.6
    assert m["green_rule"]["space"] == "HSV_OpenCV"


def test_count_green_empty_mask():
    img = np.zeros((40, 40, 3), dtype=np.uint8)
    mask = np.zeros((40, 40), dtype=np.uint8)
    m = count_green_in_mask(img, mask)
    assert m["mask_area_px"] == 0
    assert m["green_pixels_in_mask"] == 0
    assert "empty_mask" in m["quality_flags"]


def test_count_green_only_inside_mask():
    h, w = 40, 40
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = (40, 200, 40)  # all green
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[10:30, 10:30] = 255
    m = count_green_in_mask(img, mask)
    assert m["mask_area_px"] == 400
    assert m["green_pixels_in_mask"] == 400
    assert m["green_fraction_in_mask"] == pytest.approx(1.0)


def test_scale_points_to_frame():
    pts = [[100, 50], [200, 100]]
    out = scale_points_to_frame(pts, src_w=400, src_h=200, dst_w=800, dst_h=400)
    assert out[0] == [200.0, 100.0]
    assert out[1] == [400.0, 200.0]


def test_default_growth_interval_is_one_hour():
    from basebuddy.plugins.plant_health.sam_growth import (
        DEFAULT_GROWTH_INTERVAL_S,
        growth_config,
    )

    assert DEFAULT_GROWTH_INTERVAL_S == 3600
    cfg = growth_config({"camera_id": 0})
    assert cfg["interval_s"] == 3600
    assert cfg["enabled"] is True
    # Explicit monitor setting still wins
    cfg2 = growth_config({"sam_growth": {"interval_s": 7200}})
    assert cfg2["interval_s"] == 7200


def test_select_mask_gif_samples_skips_low_light():
    from basebuddy.plugins.plant_health.mask_gif import select_mask_gif_samples

    now = 2_000_000_000.0
    samples = [
        {"sampled_at": now - 3600, "mask_path": "/tmp/a.jpg", "ok": True, "L_frame": 0.5},
        {
            "sampled_at": now - 7200,
            "mask_path": "/tmp/b.jpg",
            "ok": True,
            "L_frame": 0.02,
            "quality_flags": ["L_frame_low"],
        },
        {
            "sampled_at": now - 10800,
            "mask_path": "/tmp/c.jpg",
            "ok": True,
            "quality_flags": ["plant_desaturated"],
        },
    ]
    picked = select_mask_gif_samples(samples, max_frames=48, recent_s=72 * 3600, now=now)
    assert len(picked) == 1
    assert picked[0]["mask_path"] == "/tmp/a.jpg"


def test_extract_mask_from_overlay_recovers_shape():
    from basebuddy.plugins.plant_health.mask_gif import (
        extract_mask_from_overlay,
        render_mask_frame,
    )

    frame = np.full((80, 120, 3), 180, dtype=np.uint8)
    mask = np.zeros((80, 120), dtype=np.uint8)
    mask[10:70, 40:100] = 255
    overlay = render_mask_frame(mask, frame, mode="overlay", max_side=0, label=None)
    recovered = extract_mask_from_overlay(overlay)
    overlap = np.sum((recovered > 0) & (mask > 0))
    union = np.sum((recovered > 0) | (mask > 0))
    assert overlap / max(1, union) > 0.7
    assert np.sum(recovered[10:70, 40:100] > 0) > 0.6 * 60 * 60


def test_silhouette_path_for_overlay():
    from basebuddy.plugins.plant_health.mask_gif import silhouette_path_for_overlay

    assert silhouette_path_for_overlay("stills/cam/mask_123.jpg") == "stills/cam/sil_123.jpg"


def test_write_white_silhouette_is_binary(tmp_path):
    from basebuddy.plugins.plant_health.mask_gif import write_white_silhouette

    mask = np.zeros((40, 50), dtype=np.uint8)
    mask[5:35, 10:40] = 255
    out = tmp_path / "sil.jpg"
    write_white_silhouette(mask, str(out), max_side=50)
    img = cv2.imread(str(out))
    assert img is not None
    # Background near black, plant near white
    assert img[2, 2].mean() < 20
    assert img[20, 25].mean() > 230


def test_frame_is_low_light():
    from basebuddy.plugins.plant_health.mask_gif import frame_is_low_light

    dark = np.full((20, 20, 3), 4, dtype=np.uint8)
    bright = np.full((20, 20, 3), 180, dtype=np.uint8)
    assert frame_is_low_light(dark) is True
    assert frame_is_low_light(bright) is False


def test_select_mask_gif_samples_recent_and_daily():
    from basebuddy.plugins.plant_health.mask_gif import select_mask_gif_samples

    now = 2_000_000_000.0
    samples = []
    # 5 recent hourly
    for i in range(5):
        samples.append({
            "sampled_at": now - i * 3600,
            "mask_path": f"/tmp/m{i}.jpg",
            "ok": True,
        })
    # Older: 4 days with 3 samples each — should collapse to 1/day
    for d in range(4):
        day_base = now - (4 + d) * 86400
        for h in (0, 6, 12):
            samples.append({
                "sampled_at": day_base + h * 3600,
                "mask_path": f"/tmp/old{d}_{h}.jpg",
                "ok": True,
            })
    picked = select_mask_gif_samples(samples, max_frames=48, recent_s=72 * 3600, now=now)
    assert len(picked) == 5 + 4  # all recent + one per older day
    assert picked[0]["sampled_at"] <= picked[-1]["sampled_at"]


def test_select_mask_gif_samples_thins_when_over_max():
    from basebuddy.plugins.plant_health.mask_gif import select_mask_gif_samples

    now = 2_000_000_000.0
    samples = [
        {"sampled_at": now - i * 3600, "mask_path": f"/tmp/x{i}.jpg", "ok": True}
        for i in range(100)
    ]
    picked = select_mask_gif_samples(samples, max_frames=10, recent_s=7 * 86400, now=now)
    assert len(picked) <= 10
    assert picked[0]["sampled_at"] == min(s["sampled_at"] for s in samples)
    assert picked[-1]["sampled_at"] == max(s["sampled_at"] for s in samples)


def test_render_mask_frame_silhouette_and_overlay():
    from basebuddy.plugins.plant_health.mask_gif import render_mask_frame

    mask = np.zeros((40, 60), dtype=np.uint8)
    mask[10:30, 15:45] = 255
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[:] = (30, 40, 50)
    sil = render_mask_frame(mask, None, mode="silhouette", max_side=32, label="Jan 1")
    assert sil.shape[0] <= 32 and sil.shape[1] <= 32
    assert sil.ndim == 3
    ov = render_mask_frame(mask, frame, mode="overlay", max_side=48)
    assert ov.shape[0] <= 48
    # Plant pixels should differ from background
    assert not np.array_equal(ov[20, 30], ov[0, 0])


def test_frames_bgr_to_gif_bytes_roundtrip():
    from basebuddy.plugins.plant_health.mask_gif import frames_bgr_to_gif_bytes

    frames = [
        np.full((24, 32, 3), 40, dtype=np.uint8),
        np.full((24, 32, 3), 80, dtype=np.uint8),
        np.full((24, 32, 3), 120, dtype=np.uint8),
    ]
    data = frames_bgr_to_gif_bytes(frames, duration_ms=100)
    assert isinstance(data, (bytes, bytearray))
    assert data[:6] in (b"GIF89a", b"GIF87a")
    assert len(data) > 50


class _MemDb:
    """Minimal stand-in for analytics_db with real SQLite."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = None

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
def sam_db(monkeypatch):
    db = _MemDb()
    monkeypatch.setattr(series_mod, "_db", lambda: db)
    # Avoid pulling full plant table init (which needs analytics); just growth tables.
    monkeypatch.setattr(
        series_mod,
        "init_plant_tables",
        lambda conn: series_mod.ensure_sam_growth_tables(conn),
    )
    with db._connect() as conn:
        series_mod.ensure_sam_growth_tables(conn)
        conn.commit()
    return db


def test_create_series_archives_previous(sam_db):
    s1 = series_mod.create_series(
        "m1", 0, [[10, 10]], [1], frame_w=100, frame_h=80, label="v1"
    )
    assert s1["active"] is True
    series_mod.save_growth_sample(
        s1["id"],
        "m1",
        {
            "mask_area_px": 1000,
            "mask_area_fraction": 0.1,
            "green_pixels_in_mask": 800,
            "total_mask_pixels": 1000,
            "green_fraction_in_mask": 0.8,
            "frame_w": 100,
            "frame_h": 80,
            "quality_flags": [],
        },
    )

    s2 = series_mod.create_series(
        "m1", 0, [[20, 20], [30, 30]], [1, 0], frame_w=100, frame_h=80, label="v2"
    )
    assert s2["active"] is True
    assert s2["id"] != s1["id"]

    old = series_mod.get_series(s1["id"])
    assert old["active"] is False

    # Old samples remain readable
    samples = series_mod.list_growth_samples(s1["id"])
    assert len(samples) == 1
    assert samples[0]["mask_area_px"] == 1000

    listed = series_mod.list_series("m1")
    assert len(listed) == 2
    assert listed[0]["id"] == s2["id"]  # newest first
    assert series_mod.get_active_series("m1")["id"] == s2["id"]


def test_activate_series(sam_db):
    s1 = series_mod.create_series("m1", 0, [[1, 1]], [1], label="a")
    s2 = series_mod.create_series("m1", 0, [[2, 2]], [1], label="b")
    assert series_mod.get_active_series("m1")["id"] == s2["id"]
    series_mod.activate_series(s1["id"])
    assert series_mod.get_active_series("m1")["id"] == s1["id"]
    assert series_mod.get_series(s2["id"])["active"] is False


def test_run_growth_sample_mocked_sam(sam_db, monkeypatch):
    from basebuddy.plugins.plant_health import sam_growth as growth_mod

    series = series_mod.create_series(
        "m1", 0, [[15, 15]], [1], frame_w=40, frame_h=40, label="t"
    )

    frame = np.zeros((40, 40, 3), dtype=np.uint8)
    frame[5:35, 5:35] = (40, 180, 40)
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[5:35, 5:35] = 255

    monkeypatch.setattr(
        growth_mod,
        "growth_config",
        lambda m: {
            "enabled": True,
            "interval_s": 60,
            "exemplar_interval_s": 86400,
            "area_change_ratio": 0.15,
        },
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: {"id": mid, "camera_id": 0, "sam_growth": {"enabled": True}},
    )

    def fake_capture(cam, max_width=1280, crop=None):
        return frame.copy()

    def fake_sam(fr, points, labels):
        return mask.copy(), None

    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.segmentation.capture_frame_bgr",
        fake_capture,
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.segmentation.run_sam_mask",
        fake_sam,
    )
    monkeypatch.setattr(growth_mod, "_save_exemplar", lambda *a, **k: "stills/fake.jpg")

    result = growth_mod.run_growth_sample("m1", force=True, trigger="test")
    assert result["ok"] is True
    assert result["metrics"]["mask_area_px"] == 900
    assert result["metrics"]["green_pixels_in_mask"] > 0
    samples = series_mod.list_growth_samples(series["id"])
    assert len(samples) == 1
    assert samples[0]["exemplar_path"] == "stills/fake.jpg"


def test_maybe_run_skips_when_not_due(sam_db, monkeypatch):
    from basebuddy.plugins.plant_health import sam_growth as growth_mod

    s = series_mod.create_series("m1", 0, [[1, 1]], [1])
    series_mod.save_growth_sample(
        s["id"],
        "m1",
        {"mask_area_px": 10, "green_pixels_in_mask": 5, "quality_flags": []},
        sampled_at=time.time(),
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: {
            "id": mid,
            "camera_id": 0,
            "sam_growth": {"enabled": True, "interval_s": 99999},
        },
    )
    out = growth_mod.maybe_run_growth_sample("m1")
    assert out is not None
    assert out.get("skipped") is True


def test_bootstrap_from_existing_mask_with_relative_points(sam_db, monkeypatch, tmp_path):
    """Monitors with a pre-growth SAM mask get a series without re-saving."""
    from basebuddy.plugins.plant_health import sam_growth as growth_mod

    mask = np.zeros((40, 60), dtype=np.uint8)
    mask[5:35, 10:50] = 255
    mask_path = str(tmp_path / "plant_mask.png")
    import cv2

    cv2.imwrite(mask_path, mask)

    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[5:35, 10:50] = (40, 180, 40)
    ref_path = str(tmp_path / "ref.jpg")
    cv2.imwrite(ref_path, frame)

    pattern = {
        "mask_path": mask_path,
        "pattern": {
            "mask_path": mask_path,
            "mask_shape": [40, 60],
            # Center-ish FG + one BG, relative coords
            "fg_points_relative": [[0.5, 0.5]],
            "bg_points_relative": [[0.05, 0.05]],
        },
        "reference_image": ref_path,
    }

    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: {
            "id": mid,
            "camera_id": 0,
            "segmentation": {
                "mode": "color_profile",
                "pattern_id": 0,
                "pattern_camera_id": 0,
                "mask_path": mask_path,
            },
        },
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.metrics.load_pattern_entry",
        lambda cam, pid=-1: pattern,
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.metrics.resolve_mask_path",
        lambda mon: mask_path,
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.segmentation.capture_frame_bgr",
        lambda cam, max_width=1280, crop=None: frame.copy(),
    )
    monkeypatch.setattr(growth_mod, "_save_exemplar", lambda *a, **k: "stills/boot.jpg")

    assert series_mod.list_series("m1") == []
    result = growth_mod.ensure_series_from_existing_mask("m1")
    assert result is not None
    assert result["ok"] is True
    assert result["bootstrapped"] is True
    assert result["has_anchor_points"] is True
    assert result["source"] == "pattern_relative"

    series = series_mod.get_active_series("m1")
    assert series is not None
    assert len(series["points"]) == 2
    assert series["points"][0] == [30.0, 20.0]  # 0.5 * 60, 0.5 * 40
    assert series["labels"] == [1, 0]
    assert series["initial_mask_path"] == mask_path

    samples = series_mod.list_growth_samples(series["id"])
    assert len(samples) == 1
    assert samples[0]["mask_area_px"] == int(np.sum(mask > 0))
    assert samples[0]["ok"] is True

    # Idempotent — second call does nothing
    assert growth_mod.ensure_series_from_existing_mask("m1") is None


def test_bootstrap_from_mask_bitmap_without_points(sam_db, monkeypatch, tmp_path):
    """Bitmap-only legacy masks still get a series; samples use static mask."""
    from basebuddy.plugins.plant_health import sam_growth as growth_mod

    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:25, 5:25] = 255
    mask_path = str(tmp_path / "legacy_mask.png")
    import cv2

    cv2.imwrite(mask_path, mask)
    frame = np.zeros((30, 30, 3), dtype=np.uint8)
    frame[5:25, 5:25] = (40, 180, 40)

    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.config.get_monitor",
        lambda mid: {
            "id": mid,
            "camera_id": 0,
            "segmentation": {
                "mode": "color_profile",
                "mask_path": mask_path,
            },
            "sam_growth": {"enabled": True},
        },
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.metrics.load_pattern_entry",
        lambda cam, pid=-1: None,
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.metrics.resolve_mask_path",
        lambda mon: mask_path,
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.segmentation.capture_frame_bgr",
        lambda cam, max_width=1280, crop=None: frame.copy(),
    )
    monkeypatch.setattr(
        "basebuddy.plugins.plant_health.metrics.load_saved_mask",
        lambda shape, mon: mask.copy(),
    )
    monkeypatch.setattr(growth_mod, "_save_exemplar", lambda *a, **k: None)

    result = growth_mod.ensure_series_from_existing_mask("m1")
    assert result["has_anchor_points"] is False
    assert result["source"] == "mask_bitmap"
    series = series_mod.get_active_series("m1")
    assert series["points"] == []
    assert "static_mask_no_anchors" in (result["first_sample"]["metrics"]["quality_flags"])

    # Scheduled/manual sample still works via static mask fallback
    out = growth_mod.run_growth_sample("m1", series_id=series["id"], force=True, trigger="test")
    assert out["ok"] is True
    assert out.get("static_mask") is True
    assert out["metrics"]["mask_area_px"] == 400


def test_update_series_anchors_keeps_id(sam_db):
    s = series_mod.create_series("m1", 0, [[10, 10]], [1], frame_w=100, frame_h=80, label="keep")
    updated = series_mod.update_series_anchors(
        s["id"], [[20, 20], [30, 5]], [1, 0], frame_w=120, frame_h=90, initial_mask_path="new.png"
    )
    assert updated["id"] == s["id"]
    assert updated["points"] == [[20.0, 20.0], [30.0, 5.0]]
    assert updated["labels"] == [1, 0]
    assert updated["frame_w"] == 120
    assert updated["initial_mask_path"] == "new.png"
    assert series_mod.get_active_series("m1")["id"] == s["id"]


def test_create_series_from_save_updates_existing(sam_db, monkeypatch):
    from basebuddy.plugins.plant_health import sam_growth as growth_mod

    first = series_mod.create_series("m1", 0, [[1, 1]], [1], label="orig")
    monkeypatch.setattr(growth_mod, "record_sample_from_mask", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(growth_mod, "run_growth_sample", lambda *a, **k: {"ok": True})
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    mask = np.ones((10, 10), dtype=np.uint8)
    out = growth_mod.create_series_from_save(
        "m1",
        0,
        [[8, 8]],
        [1],
        frame_w=10,
        frame_h=10,
        frame=frame,
        mask=mask,
        force_new=False,
    )
    assert out["updated_existing"] is True
    assert out["new_series"] is False
    assert out["series"]["id"] == first["id"]
    assert series_mod.get_active_series("m1")["id"] == first["id"]
    assert series_mod.list_series("m1")[0]["points"] == [[8.0, 8.0]]

    out2 = growth_mod.create_series_from_save(
        "m1", 0, [[9, 9]], [1], force_new=True, take_first_sample=False
    )
    assert out2["new_series"] is True
    assert out2["series"]["id"] != first["id"]
    assert series_mod.get_active_series("m1")["id"] == out2["series"]["id"]


def test_row_to_sample_flags_lights_out(sam_db):
    s = series_mod.create_series("m1", 0, [[1, 1]], [1])
    series_mod.save_growth_sample(
        s["id"],
        "m1",
        {
            "mask_area_px": 150000,
            "green_pixels_in_mask": 0,
            "green_fraction_in_mask": 0.0,
            "quality_flags": ["low_green_in_mask", "plant_desaturated"],
            "L_frame": 0.48,
            "L_plant": 0.81,
        },
    )
    samples = series_mod.list_growth_samples(s["id"])
    assert len(samples) == 1
    assert samples[0]["lights_out"] is True
    assert samples[0]["L_plant"] == 0.81
    assert samples[0]["L_frame"] == 0.48


def test_sample_window_hours_and_cap():
    now = 1_700_000_000.0
    limit, since = sample_window({"hours": 24 * 14, "limit": 100000}, now=now)
    assert limit == 100000
    assert since == pytest.approx(now - 14 * 24 * 3600)
    limit, since = sample_window({}, now=now)
    assert since is None
    assert limit == series_mod.SAMPLE_QUERY_CAP
    limit, _since = sample_window({"limit": 10**9}, now=now)
    assert limit == series_mod.SAMPLE_QUERY_CAP
