"""Tests for compact SAM playback board composition and GIF/MOV export."""
from __future__ import annotations

import shutil

import cv2
import numpy as np
import pytest

from basebuddy.plugins.plant_health.playback_export import (
    compose_board_frame,
    duration_ms_for_speed,
    is_lights_out,
    playback_rows_from_samples,
    select_export_indices,
    write_gif_export,
    write_mov_export,
)


def _overlay(h=80, w=100):
    img = np.full((h, w, 3), 40, dtype=np.uint8)
    img[10:70, 20:80] = (180, 140, 20)
    return img


def _sil(h=80, w=100):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[10:70, 20:80] = 255
    return img


def _samples(n=6, start=1_700_000_000):
    rows = []
    for i in range(n):
        rows.append({
            "ok": True,
            "sampled_at": start + i * 3600,
            "mask_area_px": 10000 + i * 400,
            "green_pixels_in_mask": 6000 + i * 200,
            "green_fraction_in_mask": 0.5 + i * 0.02,
            "lights_out": False,
            "mask_path": f"/tmp/nope_overlay_{i}.jpg",
        })
    return rows


def test_duration_ms_for_speed():
    assert duration_ms_for_speed(1) == 420
    assert duration_ms_for_speed(2) == 210
    assert duration_ms_for_speed(0.5) == 840
    assert duration_ms_for_speed(8) < duration_ms_for_speed(1)


def test_select_export_indices_keeps_ends():
    idxs = select_export_indices(100, 10)
    assert idxs[0] == 0
    assert idxs[-1] == 99
    assert len(idxs) <= 10


def test_is_lights_out_flags():
    assert is_lights_out({"lights_out": True}) is True
    assert is_lights_out({
        "quality_flags": ["plant_desaturated"],
        "green_pixels_in_mask": 0,
    }) is True
    assert is_lights_out({"ok": True, "green_pixels_in_mask": 10}) is False


def test_compose_board_frame_layout():
    frames = [{"sampled_at": 1_700_000_000 + i * 3600} for i in range(8)]
    board = compose_board_frame(
        _overlay(),
        _sil(),
        _samples(8),
        frames,
        3,
        title="kk",
        size=(640, 360),
    )
    assert board.shape == (360, 640, 3)
    # Not a flat color — images and plots painted
    assert board.std() > 8
    # Header title pixels near top-left should be bright-ish (white text)
    assert board[20:40, 16:80].mean() > 20


def test_compose_board_gif_roundtrip(tmp_path):
    frames_meta = [{"sampled_at": 1_700_000_000 + i * 3600} for i in range(4)]
    samples = _samples(4)
    boards = [
        compose_board_frame(_overlay(), _sil(), samples, frames_meta, i, title="kk", size=(480, 280))
        for i in range(4)
    ]
    out = tmp_path / "board.gif"
    write_gif_export(boards, str(out), duration_ms=80)
    data = out.read_bytes()
    assert data[:6] in (b"GIF89a", b"GIF87a")
    assert out.stat().st_size > 200


def test_playback_rows_skip_lights_out(tmp_path):
    ov = tmp_path / "mask_1.jpg"
    cv2.imwrite(str(ov), _overlay())
    samples = [
        {
            "ok": True,
            "sampled_at": 10,
            "mask_path": str(ov),
            "lights_out": True,
            "mask_area_px": 1,
        },
        {
            "ok": True,
            "sampled_at": 20,
            "mask_path": str(ov),
            "lights_out": False,
            "mask_area_px": 2,
        },
    ]
    rows = playback_rows_from_samples(samples)
    assert len(rows) == 1
    assert rows[0]["sampled_at"] == 20
    assert rows[0]["overlay_path"] == str(ov)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_write_mov_export(tmp_path):
    boards = [
        compose_board_frame(_overlay(), _sil(), _samples(3), [{"sampled_at": 1_700_000_000 + i * 3600} for i in range(3)], i, size=(320, 240))
        for i in range(3)
    ]
    out = tmp_path / "board.mov"
    write_mov_export(boards, str(out), fps=4)
    assert out.is_file()
    assert out.stat().st_size > 200
