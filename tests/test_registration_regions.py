"""Tests for per-camera registration regions (multiview mask constraints)."""
import json
import os

import numpy as np
import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    # get_repo_root() reads BASEBUDDY_REPO_ROOT on every call, so this
    # isolates region files under a temp dir.
    monkeypatch.setenv("BASEBUDDY_REPO_ROOT", str(tmp_path))
    yield tmp_path


class TestRegionStore:
    def test_load_defaults_when_unset(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import load_regions

        config = load_regions(3)
        assert config == {"exclude": [], "include": [], "use_seg_mask": False}

    def test_save_and_load_roundtrip(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import load_regions, save_regions

        saved = save_regions(1, {
            "exclude": [[0.0, 0.0, 0.5, 0.1]],
            "include": [[0.2, 0.2, 0.9, 0.9]],
            "use_seg_mask": True,
        })
        assert saved["exclude"] == [[0.0, 0.0, 0.5, 0.1]]
        assert load_regions(1) == saved

    def test_sanitizes_bad_boxes(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import save_regions

        saved = save_regions(2, {
            "exclude": [
                [-0.5, 0.0, 0.5, 2.0],        # clamped
                [0.3, 0.3, 0.3001, 0.3001],   # degenerate -> dropped
                ["bad", 0, 1, 1],             # non-numeric -> dropped
            ],
            "include": "not-a-list",
        })
        assert saved["exclude"] == [[0.0, 0.0, 0.5, 1.0]]
        assert saved["include"] == []

    def test_clear(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            clear_regions, load_regions, save_regions,
        )

        save_regions(4, {"exclude": [[0, 0, 0.5, 0.5]]})
        clear_regions(4)
        assert load_regions(4)["exclude"] == []


class TestBuildMask:
    def test_no_constraints_returns_none(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import build_registration_mask

        assert build_registration_mask(0, (100, 200)) is None

    def test_exclude_box_zeroes_area(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {"exclude": [[0.0, 0.0, 0.5, 0.1]]})  # top-left strip
        mask = build_registration_mask(0, (100, 200))
        assert mask is not None and mask.shape == (100, 200)
        assert mask[5, 50] == 0        # inside exclude box
        assert mask[50, 100] == 255    # outside

    def test_include_boxes_restrict(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {"include": [[0.25, 0.25, 0.75, 0.75]]})
        mask = build_registration_mask(0, (100, 100))
        assert mask[50, 50] == 255     # inside include
        assert mask[10, 10] == 0       # outside include

    def test_exclude_wins_over_include(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {
            "include": [[0.0, 0.0, 1.0, 1.0]],
            "exclude": [[0.4, 0.4, 0.6, 0.6]],
        })
        mask = build_registration_mask(0, (100, 100))
        assert mask[50, 50] == 0
        assert mask[10, 10] == 255

    def test_seg_mask_intersection(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {"exclude": [[0.0, 0.0, 0.2, 0.2]]})

        def loader(cam_id, shape):
            seg = np.zeros(shape, dtype=np.uint8)
            seg[:, 50:] = 255  # right half is plant
            return seg

        mask = build_registration_mask(0, (100, 100), use_seg_mask=True,
                                       seg_mask_loader=loader)
        assert mask[50, 25] == 0       # left half removed by seg mask
        assert mask[50, 75] == 255     # right half kept
        assert mask[10, 10] == 0       # exclude region

    def test_seg_mask_skipped_when_disabled(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {"exclude": [[0.0, 0.0, 0.2, 0.2]]})

        def loader(cam_id, shape):
            return np.zeros(shape, dtype=np.uint8)  # would blank everything

        mask = build_registration_mask(0, (100, 100), use_seg_mask=False,
                                       seg_mask_loader=loader)
        assert mask[50, 50] == 255     # seg mask ignored
        assert mask[10, 10] == 0

    def test_all_masked_returns_none(self, repo_root):
        from basebuddy.modules.multiview.registration_regions import (
            build_registration_mask, save_regions,
        )

        save_regions(0, {"exclude": [[0.0, 0.0, 1.0, 1.0]]})
        assert build_registration_mask(0, (50, 50)) is None


class TestRegionsApi:
    @pytest.fixture
    def client(self, repo_root):
        from basebuddy.app import create_app

        app, _socketio = create_app()
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_roundtrip(self, client):
        resp = client.post("/api/multiview/regions/5", json={
            "exclude": [[0.0, 0.9, 0.4, 1.0]],
            "use_seg_mask": False,
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        resp = client.get("/api/multiview/regions/5")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["regions"]["exclude"] == [[0.0, 0.9, 0.4, 1.0]]

        resp = client.delete("/api/multiview/regions/5")
        assert resp.get_json()["ok"] is True
        resp = client.get("/api/multiview/regions/5")
        assert resp.get_json()["regions"]["exclude"] == []
