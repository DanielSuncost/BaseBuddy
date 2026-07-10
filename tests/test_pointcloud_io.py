"""Tests for binary PLY write/read and voxel downsampling."""
import numpy as np

from basebuddy.modules.multiview.pointcloud_io import load_ply, save_ply, voxel_downsample


def test_ply_roundtrip(tmp_path):
    rng = np.random.default_rng(1)
    pts = rng.normal(size=(1000, 3)).astype(np.float32)
    cols = rng.integers(0, 256, size=(1000, 3)).astype(np.uint8)

    path = str(tmp_path / 'cloud.ply')
    written = save_ply(path, pts, cols)
    assert written == 1000

    rpts, rcols = load_ply(path)
    assert rpts.shape == (1000, 3)
    np.testing.assert_allclose(rpts, pts, rtol=1e-6)
    np.testing.assert_array_equal(rcols, cols)


def test_save_ply_drops_nonfinite(tmp_path):
    pts = np.array([[0, 0, 0], [np.nan, 1, 1], [2, 2, 2]], dtype=np.float32)
    cols = np.full((3, 3), 128, dtype=np.uint8)
    written = save_ply(str(tmp_path / 'c.ply'), pts, cols)
    assert written == 2


def test_save_ply_accepts_float_colors(tmp_path):
    pts = np.zeros((4, 3), dtype=np.float32)
    pts[:, 0] = np.arange(4)
    cols = np.ones((4, 3), dtype=np.float32) * 0.5  # [0,1] floats
    save_ply(str(tmp_path / 'c.ply'), pts, cols)
    _, rcols = load_ply(str(tmp_path / 'c.ply'))
    assert 120 <= rcols[0][0] <= 135


def test_voxel_downsample_caps_points():
    rng = np.random.default_rng(2)
    pts = rng.uniform(0, 1, size=(50_000, 3))
    cols = np.zeros((50_000, 3), dtype=np.uint8)
    dpts, dcols = voxel_downsample(pts, cols, max_points=10_000)
    assert len(dpts) <= 10_000
    assert len(dpts) == len(dcols)
    # Should keep good spatial coverage.
    assert dpts.min() < 0.05 and dpts.max() > 0.95


def test_voxel_downsample_noop_when_small():
    pts = np.zeros((100, 3))
    cols = np.zeros((100, 3), dtype=np.uint8)
    dpts, _ = voxel_downsample(pts, cols, max_points=1000)
    assert len(dpts) == 100
