"""
Point cloud I/O helpers for multiview reconstruction.

Binary little-endian PLY read/write (about 5x smaller than the ASCII PLY the
legacy pipeline emitted) plus voxel-grid downsampling so browser viewers get
a bounded number of points.
"""
from __future__ import annotations

import numpy as np

# Cap written clouds so PLY files stay browser-friendly (~24 MB binary).
MAX_POINTS = 1_500_000

_PLY_HEADER = """ply
format binary_little_endian 1.0
element vertex {count}
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
"""


def voxel_downsample(points: np.ndarray, colors: np.ndarray,
                     max_points: int = MAX_POINTS) -> tuple:
    """Reduce a cloud below max_points using a voxel grid (keeps one point
    per occupied voxel, averaging would blur colors so we keep the first)."""
    n = len(points)
    if n <= max_points:
        return points, colors

    bbox_min = points.min(axis=0)
    bbox_max = points.max(axis=0)
    extent = float(np.max(bbox_max - bbox_min)) or 1.0

    # Search a voxel size that lands under the cap (few iterations suffice).
    voxel = extent / 512.0
    for _ in range(12):
        keys = np.floor((points - bbox_min) / voxel).astype(np.int64)
        _, idx = np.unique(keys, axis=0, return_index=True)
        if len(idx) <= max_points:
            idx.sort()
            return points[idx], colors[idx]
        voxel *= 1.5

    # Fallback: random subsample.
    sel = np.random.default_rng(0).choice(n, size=max_points, replace=False)
    sel.sort()
    return points[sel], colors[sel]


def save_ply(path: str, points: np.ndarray, colors: np.ndarray) -> int:
    """Write a binary PLY. Returns the number of points written."""
    points = np.asarray(points, dtype=np.float32).reshape(-1, 3)
    colors = np.asarray(colors).reshape(-1, 3)
    if colors.dtype != np.uint8:
        # Accept float colors in [0,1] or [0,255].
        colors = np.clip(colors * 255.0 if colors.max() <= 1.0 else colors, 0, 255).astype(np.uint8)

    finite = np.isfinite(points).all(axis=1)
    points, colors = points[finite], colors[finite]
    points, colors = voxel_downsample(points, colors)

    record = np.empty(len(points), dtype=[
        ('x', '<f4'), ('y', '<f4'), ('z', '<f4'),
        ('red', 'u1'), ('green', 'u1'), ('blue', 'u1'),
    ])
    record['x'], record['y'], record['z'] = points[:, 0], points[:, 1], points[:, 2]
    record['red'], record['green'], record['blue'] = colors[:, 0], colors[:, 1], colors[:, 2]

    with open(path, 'wb') as f:
        f.write(_PLY_HEADER.format(count=len(points)).encode('ascii'))
        record.tofile(f)
    return len(points)


def load_ply(path: str) -> tuple:
    """Read xyz + rgb from an ASCII or binary-little-endian PLY written by
    this app (also tolerates extra float properties like confidence)."""
    with open(path, 'rb') as f:
        header_lines = []
        while True:
            line = f.readline().decode('ascii', 'replace').strip()
            header_lines.append(line)
            if line == 'end_header':
                break
            if len(header_lines) > 100:
                raise ValueError('Malformed PLY header')

        fmt = next((l.split()[1] for l in header_lines if l.startswith('format')), 'ascii')
        count = int(next(l.split()[-1] for l in header_lines if l.startswith('element vertex')))
        props = [l.split() for l in header_lines if l.startswith('property')]

        type_map = {'float': '<f4', 'float32': '<f4', 'double': '<f8',
                    'uchar': 'u1', 'uint8': 'u1', 'int': '<i4', 'uint': '<u4'}
        names = [p[2] for p in props]
        if fmt == 'ascii':
            data = np.loadtxt(f, max_rows=count, dtype=np.float64)
            data = np.atleast_2d(data)
            cols = {name: data[:, i] for i, name in enumerate(names)}
        else:
            dtype = np.dtype([(p[2], type_map[p[1]]) for p in props])
            raw = np.fromfile(f, dtype=dtype, count=count)
            cols = {name: raw[name] for name in names}

    points = np.stack([cols['x'], cols['y'], cols['z']], axis=1).astype(np.float32)
    if 'red' in cols:
        colors = np.stack([cols['red'], cols['green'], cols['blue']], axis=1).astype(np.uint8)
    else:
        colors = np.full((len(points), 3), 200, dtype=np.uint8)
    return points, colors
