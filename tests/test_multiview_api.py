"""Contract tests for the modern multiview reconstruction API."""
import json
import os

import numpy as np
import pytest
from flask import Flask

import basebuddy.routes.multiview_3d as mv
from basebuddy.modules.multiview.pointcloud_io import save_ply


@pytest.fixture
def client(tmp_path, monkeypatch):
    recon_dir = tmp_path / 'reconstructions'
    recon_dir.mkdir()
    monkeypatch.setattr(mv, 'RECONSTRUCTIONS_DIR', str(recon_dir))

    app = Flask(__name__)
    app.register_blueprint(mv.multiview_3d_bp)
    return app.test_client()


def _make_reconstruction(recon_id='recon_20260704_120000_sfm', n=500):
    """Write a PLY + metadata pair into the (patched) reconstructions dir."""
    rng = np.random.default_rng(0)
    pts = rng.uniform(0, 1, size=(n, 3)).astype(np.float32)
    cols = rng.integers(0, 256, size=(n, 3)).astype(np.uint8)
    ply_file = f'{recon_id}.ply'
    save_ply(os.path.join(mv.RECONSTRUCTIONS_DIR, ply_file), pts, cols)
    meta = {
        'id': recon_id,
        'timestamp': '2026-07-04T12:00:00',
        'engine': 'sfm',
        'camera_ids': [0, 1],
        'num_points': n,
        'ply_file': ply_file,
        'metrics': None,
    }
    with open(os.path.join(mv.RECONSTRUCTIONS_DIR, f'{recon_id}.json'), 'w') as f:
        json.dump(meta, f)
    return recon_id


def test_engines_endpoint(client):
    data = client.get('/api/multiview/engines').get_json()
    assert data['ok'] is True
    assert 'cuda_available' in data
    ids = [e['id'] for e in data['engines']]
    assert 'auto' in ids and 'vggt' in ids and 'sfm' in ids


def test_start_requires_two_cameras(client):
    resp = client.post('/api/multiview/reconstruct/start',
                       json={'camera_ids': [0], 'engine': 'sfm'})
    assert resp.status_code == 400
    assert resp.get_json()['ok'] is False


def test_start_rejects_unavailable_engine(client):
    resp = client.post('/api/multiview/reconstruct/start',
                       json={'camera_ids': [0, 1], 'engine': 'not-a-real-engine'})
    assert resp.status_code == 400


def test_job_polling_unknown_id(client):
    resp = client.get('/api/multiview/jobs/deadbeef')
    assert resp.status_code == 404


def test_cloud_serving_and_traversal_guard(client):
    recon_id = _make_reconstruction()
    resp = client.get(f'/api/multiview/cloud/{recon_id}.ply')
    assert resp.status_code == 200
    assert resp.data.startswith(b'ply')

    assert client.get('/api/multiview/cloud/..%2F..%2Fetc%2Fpasswd').status_code in (400, 404)
    assert client.get('/api/multiview/cloud/notaply.txt').status_code == 400


def test_reconstructions_listing_normalizes_meta(client):
    _make_reconstruction()
    data = client.get('/api/multiview/reconstructions').get_json()
    assert data['ok'] is True
    assert len(data['reconstructions']) == 1
    meta = data['reconstructions'][0]
    assert meta['ply_url'].startswith('/api/multiview/cloud/')
    assert meta['download_url'].startswith('/api/multiview/download/')
    assert meta['size_bytes'] > 0


def test_scale_endpoint_computes_metric_measurements(client):
    recon_id = _make_reconstruction(n=5000)
    resp = client.post(f'/api/multiview/reconstruction/{recon_id}/scale',
                       json={'known_distance_units': 2.0,
                             'known_distance_m': 1.0,
                             'up_axis': '+y'})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data['ok'] is True
    assert data['metrics']['units'] == 'metric'
    assert data['metrics']['scale_m_per_unit'] == pytest.approx(0.5)

    # Persisted: growth series now includes this reconstruction.
    series = client.get('/api/multiview/growth-series').get_json()['series']
    assert [s['id'] for s in series] == [recon_id]


def test_scale_endpoint_validates_input(client):
    recon_id = _make_reconstruction()
    assert client.post(f'/api/multiview/reconstruction/{recon_id}/scale',
                       json={'known_distance_units': 0, 'known_distance_m': 1}).status_code == 400
    assert client.post(f'/api/multiview/reconstruction/{recon_id}/scale',
                       json={'scale_m_per_unit': 0.5, 'up_axis': '+q'}).status_code == 400
    assert client.post('/api/multiview/reconstruction/missing/scale',
                       json={'scale_m_per_unit': 0.5}).status_code == 404


def test_delete_reconstruction(client):
    recon_id = _make_reconstruction()
    assert client.delete(f'/api/multiview/reconstruction/{recon_id}').get_json()['ok'] is True
    assert client.get('/api/multiview/reconstructions').get_json()['reconstructions'] == []
    assert client.delete(f'/api/multiview/reconstruction/{recon_id}').status_code == 404


class _StubEngine:
    """Minimal engine standing in for VGGT/Pi3/DUSt3R in pipeline tests."""
    id = 'stub'

    def reconstruct(self, images, masks=None, progress_cb=None):
        from basebuddy.modules.multiview.engines.base import EngineResult
        rng = np.random.default_rng(3)
        n = 2000
        return EngineResult(points=rng.uniform(0, 1, size=(n, 3)).astype(np.float32),
                            colors=rng.integers(0, 256, size=(n, 3)).astype(np.uint8))


def test_full_job_pipeline_persists_reconstruction(client):
    """Engine output -> metrics -> PLY + metadata -> visible in listing."""
    progress = []
    result = mv._run_reconstruction_job(
        lambda pct, msg='': progress.append(pct),
        _StubEngine(),
        images=[np.zeros((10, 10, 3), np.uint8)] * 2,
        masks=None,
        camera_ids=[0, 1])

    meta = result['reconstruction']
    assert meta['engine'] == 'stub'
    assert meta['num_points'] == 2000
    assert meta['metrics'] is not None
    assert meta['metrics']['units'] == 'relative'
    assert progress and max(progress) >= 95

    listing = client.get('/api/multiview/reconstructions').get_json()['reconstructions']
    assert any(r['id'] == meta['id'] for r in listing)

    ply = client.get(meta['ply_url'])
    assert ply.status_code == 200 and ply.data.startswith(b'ply')
