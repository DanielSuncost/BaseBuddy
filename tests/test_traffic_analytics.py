"""Tests for traffic track storage with class names and the sources query."""
import time

import pytest

from basebuddy.modules.database import AnalyticsDB


@pytest.fixture()
def db(tmp_path):
    return AnalyticsDB(db_path=str(tmp_path / "test.db"))


def _track_points(t0=None):
    t0 = t0 or time.time()
    return [(100.0, 100.0, t0), (200.0, 150.0, t0 + 2.0)]


class TestTrafficClassNames:
    def test_save_and_query_with_class(self, db):
        assert db.save_traffic_track(3, 1, _track_points(), 50,
                                     region_label="main-st", class_name="car")
        assert db.save_traffic_track(3, 2, _track_points(), 50,
                                     region_label="main-st", class_name="truck")

        tracks = db.get_recent_traffic_tracks(3)
        assert len(tracks) == 2
        assert {t["class_name"] for t in tracks} == {"car", "truck"}

        only_cars = db.get_recent_traffic_tracks(3, class_name="car")
        assert len(only_cars) == 1
        assert only_cars[0]["class_name"] == "car"

    def test_hourly_stats_class_filter(self, db):
        db.save_traffic_track(0, 1, _track_points(), 50, class_name="car")
        db.save_traffic_track(0, 2, _track_points(), 50, class_name="bus")

        date = time.strftime("%Y-%m-%d")
        all_rows = db.get_traffic_hourly_stats(date, 0)
        assert sum(r["count"] for r in all_rows) == 2
        car_rows = db.get_traffic_hourly_stats(date, 0, class_name="car")
        assert sum(r["count"] for r in car_rows) == 1

    def test_class_optional(self, db):
        assert db.save_traffic_track(1, 1, _track_points(), 50)
        tracks = db.get_recent_traffic_tracks(1)
        assert tracks[0]["class_name"] == ""


class TestTrafficSources:
    def test_sources_grouping(self, db):
        db.save_traffic_track(0, 1, _track_points(), 50, region_label="a", class_name="car")
        db.save_traffic_track(0, 2, _track_points(), 50, region_label="a", class_name="car")
        db.save_traffic_track(5, 3, _track_points(), 50, region_label="b", class_name="bus")

        sources = db.get_traffic_sources()
        by_cam = {}
        for s in sources:
            by_cam.setdefault(s["camera_id"], []).append(s)

        assert by_cam[0][0]["track_count"] == 2
        assert by_cam[0][0]["region_label"] == "a"
        assert by_cam[5][0]["class_name"] == "bus"

    def test_sources_empty(self, db):
        assert db.get_traffic_sources() == []


class TestTrafficPaths:
    def test_paths_window_filter(self, db):
        now = time.time()
        db.save_traffic_track(2, 1, _track_points(now - 30), 50, class_name="car")
        db.save_traffic_track(2, 2, _track_points(now - 7200), 50, class_name="car")

        recent = db.get_traffic_paths(2, now - 3600, now + 10)
        assert len(recent) == 1
        assert len(recent[0]["points"]) == 2
        assert recent[0]["points"][0]["x"] == 100.0

        both = db.get_traffic_paths(2, now - 86400, now + 10)
        assert len(both) == 2

    def test_paths_class_filter(self, db):
        db.save_traffic_track(4, 1, _track_points(), 50, class_name="car")
        db.save_traffic_track(4, 2, _track_points(), 50, class_name="person")
        now = time.time()
        cars = db.get_traffic_paths(4, now - 3600, now + 10, class_name="car")
        assert len(cars) == 1
        assert cars[0]["class_name"] == "car"

    def test_paths_other_camera_excluded(self, db):
        db.save_traffic_track(6, 1, _track_points(), 50)
        now = time.time()
        assert db.get_traffic_paths(7, now - 3600, now + 10) == []


class TestTrafficSourcesApi:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BASEBUDDY_REPO_ROOT", str(tmp_path))
        from basebuddy.app import create_app

        app, _socketio = create_app()
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client

    def test_sources_endpoint(self, client):
        resp = client.get("/api/traffic/sources")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["cameras"], list)
        for cam in data["cameras"]:
            assert {"id", "name", "enabled", "region_labels", "classes", "track_count"} <= set(cam)

    def test_flow_map_requires_cam(self, client):
        resp = client.get("/api/traffic/flow-map")
        assert resp.status_code == 400

    def test_flow_map_returns_jpeg(self, client):
        resp = client.get("/api/traffic/flow-map?cam=0&minutes=60")
        assert resp.status_code == 200
        assert resp.mimetype == "image/jpeg"
        assert resp.data[:2] == b"\xff\xd8"  # JPEG magic
