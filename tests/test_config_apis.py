"""Round-trip tests for the config APIs (thresholds, classes, tracking, ignored).

Each test POSTs a change through the Flask API, then verifies it was persisted
to config.txt (in an isolated temp repo root) and is returned by a fresh GET.
"""
import json
import os

import pytest


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    """Flask test client with BASEBUDDY_REPO_ROOT pointed at a temp dir.

    The config APIs persist via upsert_config_exports(get_repo_root(), ...),
    and get_repo_root() honors BASEBUDDY_REPO_ROOT on every call, so pointing
    it at a temp dir keeps the real config.txt untouched.
    """
    tmp_root = tmp_path_factory.mktemp("repo_root")
    old_root = os.environ.get("BASEBUDDY_REPO_ROOT")
    os.environ["BASEBUDDY_REPO_ROOT"] = str(tmp_root)

    from basebuddy.app import create_app

    app, _socketio = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, tmp_root

    if old_root is None:
        os.environ.pop("BASEBUDDY_REPO_ROOT", None)
    else:
        os.environ["BASEBUDDY_REPO_ROOT"] = old_root


def _config_export(tmp_root, key):
    config_txt = tmp_root / "config.txt"
    if not config_txt.exists():
        return None
    for line in config_txt.read_text().splitlines():
        line = line.strip()
        if line.startswith(f'export {key}='):
            value = line.split("=", 1)[1].strip()
            if value and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


class TestThresholdsRoundTrip:
    def test_update_persists_and_reads_back(self, app_client):
        client, tmp_root = app_client

        resp = client.post(
            "/api/thresholds/camera/0",
            json={"person": 0.7, "car": 0.55, "bogus": "not-a-number"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "CLASS_THRESHOLDS"))
        assert stored["camera_0"] == {"person": 0.7, "car": 0.55}

        resp = client.get("/api/thresholds/")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["data"]["camera_0"]["person"] == 0.7

    def test_reset_removes_camera(self, app_client):
        client, tmp_root = app_client

        client.post("/api/thresholds/camera/1", json={"dog": 0.9})
        resp = client.post("/api/thresholds/camera/1/reset")
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "CLASS_THRESHOLDS"))
        assert "camera_1" not in stored


class TestDisabledClassesRoundTrip:
    def test_available_classes(self, app_client):
        client, _ = app_client
        resp = client.get("/api/classes/available")
        data = resp.get_json()
        assert data["ok"] is True
        assert "person" in data["data"]

    def test_update_persists_and_reads_back(self, app_client):
        client, tmp_root = app_client

        resp = client.post("/api/classes/disabled", json={"classes": ["kite", "frisbee"]})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "DISABLED_CLASSES"))
        assert sorted(stored) == ["frisbee", "kite"]

        resp = client.get("/api/classes/disabled")
        data = resp.get_json()
        assert data["ok"] is True
        assert sorted(data["data"]) == ["frisbee", "kite"]

    def test_clear_disabled(self, app_client):
        client, tmp_root = app_client
        client.post("/api/classes/disabled", json={"classes": []})
        stored = json.loads(_config_export(tmp_root, "DISABLED_CLASSES"))
        assert stored == []


class TestTrackingRoundTrip:
    def test_global_update_persists(self, app_client):
        client, tmp_root = app_client

        resp = client.post(
            "/api/tracking/config/global",
            json={"max_age": 45, "line_thickness": 3},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "TRACKING_CONFIG"))
        assert stored["global"]["max_age"] == 45
        assert stored["global"]["line_thickness"] == 3

        resp = client.get("/api/tracking/config")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["config"]["global"]["max_age"] == 45

    def test_camera_override_merges_with_global(self, app_client):
        client, tmp_root = app_client

        resp = client.post("/api/tracking/config/camera/0", json={"max_history": 55})
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "TRACKING_CONFIG"))
        assert stored["cameras"]["0"]["max_history"] == 55

        data = client.get("/api/tracking/config").get_json()
        cam0 = data["config"]["cameras"]["0"]
        assert cam0["max_history"] == 55
        # Non-overridden key falls back to global (set in previous test)
        assert cam0["max_age"] == 45


class TestIgnoredDetectionsRoundTrip:
    def test_add_and_remove(self, app_client):
        client, tmp_root = app_client

        resp = client.post(
            "/api/ignored-detections/0",
            json={"bbox": [10, 20, 110, 220], "class_name": "person"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        stored = json.loads(_config_export(tmp_root, "IGNORED_DETECTIONS"))
        assert len(stored["camera_0"]) == 1
        assert stored["camera_0"][0]["class_name"] == "person"

        resp = client.delete("/api/ignored-detections/0/0")
        assert resp.get_json()["ok"] is True
        stored = json.loads(_config_export(tmp_root, "IGNORED_DETECTIONS"))
        assert stored.get("camera_0", []) == []
