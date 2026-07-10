"""
Plant morphology metrics from reconstructed point clouds.

Feed-forward reconstruction (VGGT / Pi3 / DUSt3R) produces clouds in an
arbitrary scale ("relative units"). Users can calibrate a metric scale by
measuring a known object in the viewer, after which all metrics are reported
in meters / m^2 / m^3.

Axes: "up_axis" identifies which world axis points from the pot to the top of
the plant, one of '+y', '-y', '+z', '-z' (feed-forward models typically put
-y or -z up depending on camera orientation).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

UP_AXES = ('+y', '-y', '+z', '-z', '+x', '-x')

# Percentile trim so stray outlier points don't inflate the bounding extents.
_LO, _HI = 2.0, 98.0


def _axis_index(up_axis: str) -> tuple:
    axis = {'x': 0, 'y': 1, 'z': 2}[up_axis[1]]
    sign = -1.0 if up_axis[0] == '-' else 1.0
    return axis, sign


def _trim_outliers(points: np.ndarray) -> np.ndarray:
    """Drop points far outside the robust bounding box (noise from
    low-confidence pointmap regions)."""
    if len(points) < 50:
        return points
    lo = np.percentile(points, _LO, axis=0)
    hi = np.percentile(points, _HI, axis=0)
    pad = (hi - lo) * 0.1 + 1e-9
    keep = np.all((points >= lo - pad) & (points <= hi + pad), axis=1)
    return points[keep]


def compute_plant_metrics(points: np.ndarray,
                          up_axis: str = '+y',
                          scale_m_per_unit: Optional[float] = None) -> Optional[dict]:
    """
    Compute morphology metrics for a (plant) point cloud.

    Returns dict with height, canopy width/depth/area, hull volume and
    bookkeeping fields, or None when the cloud is too small to measure.
    Values are in cloud units unless scale_m_per_unit is given, in which
    case they are converted to meters (m^2 / m^3 for area / volume).
    """
    if up_axis not in UP_AXES:
        raise ValueError(f'up_axis must be one of {UP_AXES}')

    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 50:
        return None

    points = _trim_outliers(points)
    if len(points) < 50:
        return None

    axis, sign = _axis_index(up_axis)
    up_vals = points[:, axis] * sign
    plane_axes = [i for i in range(3) if i != axis]

    height = float(np.percentile(up_vals, _HI) - np.percentile(up_vals, _LO))

    w0 = points[:, plane_axes[0]]
    w1 = points[:, plane_axes[1]]
    canopy_width = float(np.percentile(w0, _HI) - np.percentile(w0, _LO))
    canopy_depth = float(np.percentile(w1, _HI) - np.percentile(w1, _LO))

    canopy_area = 0.0
    hull_volume = 0.0
    try:
        from scipy.spatial import ConvexHull
        plane_pts = np.stack([w0, w1], axis=1)
        # 2D hull of the top-down projection = canopy footprint.
        if len(np.unique(plane_pts, axis=0)) >= 3:
            canopy_area = float(ConvexHull(plane_pts).volume)  # 2D "volume" is area
        if len(np.unique(points, axis=0)) >= 4:
            hull_volume = float(ConvexHull(points).volume)
    except Exception as e:  # degenerate/coplanar clouds
        logger.warning(f'Convex hull computation failed: {e}')

    scale = float(scale_m_per_unit) if scale_m_per_unit else 1.0
    metrics = {
        'up_axis': up_axis,
        'scale_m_per_unit': float(scale_m_per_unit) if scale_m_per_unit else None,
        'units': 'metric' if scale_m_per_unit else 'relative',
        'height': height * scale,
        'canopy_width': canopy_width * scale,
        'canopy_depth': canopy_depth * scale,
        'canopy_area': canopy_area * scale * scale,
        'hull_volume': hull_volume * scale * scale * scale,
        'num_points': int(len(points)),
    }
    return metrics
