"""Tests for plant morphology metrics."""
import numpy as np
import pytest

from basebuddy.modules.multiview.plant_metrics import compute_plant_metrics, UP_AXES


def _box_cloud(w=0.4, d=0.3, h=0.8, n=5000, up_axis='+y', seed=0):
    """Uniform cloud filling a box: width x depth in the ground plane, h tall."""
    rng = np.random.default_rng(seed)
    axis = {'x': 0, 'y': 1, 'z': 2}[up_axis[1]]
    sign = -1.0 if up_axis[0] == '-' else 1.0
    plane = [i for i in range(3) if i != axis]
    pts = np.zeros((n, 3))
    pts[:, plane[0]] = rng.uniform(0, w, n)
    pts[:, plane[1]] = rng.uniform(0, d, n)
    pts[:, axis] = sign * rng.uniform(0, h, n)
    return pts


def test_relative_metrics_box():
    pts = _box_cloud()
    m = compute_plant_metrics(pts, up_axis='+y')
    assert m['units'] == 'relative'
    assert m['scale_m_per_unit'] is None
    # Percentile-trimmed extents should be close to (but under) true dims.
    assert 0.7 < m['height'] <= 0.8
    assert 0.34 < m['canopy_width'] <= 0.4
    assert 0.25 < m['canopy_depth'] <= 0.3
    assert m['hull_volume'] == pytest.approx(0.4 * 0.3 * 0.8, rel=0.15)
    assert m['canopy_area'] == pytest.approx(0.4 * 0.3, rel=0.15)


def test_metric_scaling():
    pts = _box_cloud()
    rel = compute_plant_metrics(pts, up_axis='+y')
    scaled = compute_plant_metrics(pts, up_axis='+y', scale_m_per_unit=0.5)
    assert scaled['units'] == 'metric'
    assert scaled['height'] == pytest.approx(rel['height'] * 0.5)
    assert scaled['canopy_area'] == pytest.approx(rel['canopy_area'] * 0.25)
    assert scaled['hull_volume'] == pytest.approx(rel['hull_volume'] * 0.125)


@pytest.mark.parametrize('up_axis', ['+y', '-y', '+z', '-z'])
def test_up_axis_variants(up_axis):
    pts = _box_cloud(up_axis=up_axis)
    m = compute_plant_metrics(pts, up_axis=up_axis)
    assert 0.7 < m['height'] <= 0.8


def test_outlier_robustness():
    pts = _box_cloud()
    outliers = np.array([[100.0, 100.0, 100.0], [-50.0, 0.0, 0.0]])
    m = compute_plant_metrics(np.vstack([pts, outliers]), up_axis='+y')
    # Outliers must not inflate the height beyond the true 0.8.
    assert m['height'] < 1.0


def test_too_few_points():
    assert compute_plant_metrics(np.zeros((10, 3)), up_axis='+y') is None


def test_invalid_axis():
    with pytest.raises(ValueError):
        compute_plant_metrics(_box_cloud(), up_axis='+w')
    assert '+y' in UP_AXES
