"""Detection must shed a flooded clip path and recover when results stop."""
import collections
import queue
import threading
import time
from types import SimpleNamespace

from flask import Flask

from basebuddy.core.api.health import health_api
from basebuddy.core.services.detection_watchdog import DETECTION_STALE_S, detection_health
from basebuddy.modules.grabber.detection_pipeline import DetectionPipelineMixin
from basebuddy.modules.worker import _emit_detection_result


class _Grabber(DetectionPipelineMixin):
    def __init__(self):
        self.cam_id = 0
        self.running = True
        self.detection_running = False
        self.detection_thread = None
        self.proc = None
        self.tx = None
        self.rx = None
        self.watchdog_thread = None
        self.detection_queue = collections.deque()
        self.pending_frames = {}
        self._detection_generation = 0
        self._detection_worker_started_ts = 0.0
        self._last_detection_restart_ts = 0.0
        self._abandoned_detection_thread = None
        self._stall_persist_log_ts = 0.0
        self.last_detection_ts = 0.0
        self.detection_enabled = True

    def _process_detection_queue(self, generation=None):
        return


def _fed_grabber(now, *, last_detection_ts, started_ago=200):
    grabber = _Grabber()
    grabber._detection_worker_started_ts = now - started_ago
    grabber.last_detection_ts = last_detection_ts
    grabber._last_successful_read = now
    grabber._last_detection_enqueue_ts = now
    grabber.detection_queue.append(object())
    return grabber


class TestResultQueue:
    def test_full_queue_drops_result_instead_of_blocking(self):
        results = queue.Queue(maxsize=1)
        results.put_nowait({"cmd": "result"})
        started = time.time()
        ok = _emit_detection_result(results, {"cmd": "result", "ts": 2}, timeout=0.05)
        assert ok is False
        assert time.time() - started < 0.5
        assert results.qsize() == 1


class TestStallDetection:
    def test_stale_when_capture_continues_but_results_stop(self):
        now = 1_000_000.0
        grabber = _fed_grabber(now, last_detection_ts=now - (DETECTION_STALE_S + 5))
        assert grabber._detection_is_stale(now)

    def test_not_stale_while_results_are_fresh(self):
        now = 1_000_000.0
        grabber = _fed_grabber(now, last_detection_ts=now - 1)
        assert not grabber._detection_is_stale(now)

    def test_not_stale_during_startup_grace(self):
        now = 1_000_000.0
        grabber = _fed_grabber(now, last_detection_ts=0, started_ago=5)
        assert not grabber._detection_is_stale(now)

    def test_not_stale_when_capture_is_down(self):
        now = 1_000_000.0
        grabber = _fed_grabber(now, last_detection_ts=now - 120)
        grabber._last_successful_read = now - 120
        grabber._last_detection_enqueue_ts = now - 120
        grabber.detection_queue.clear()
        assert not grabber._detection_is_stale(now)

    def test_restart_logs_and_starts_inprocess_worker(self, caplog):
        grabber = _Grabber()
        grabber.last_detection_ts = time.time() - 90
        with caplog.at_level("WARNING"):
            grabber._restart_stalled_detection(90)
        assert grabber._detection_generation == 1
        assert grabber.detection_thread is not None
        grabber.detection_thread.join(1.0)
        assert "restarting detection worker in-process" in caplog.text
        assert "Camera 0" in caplog.text

    def test_restart_does_not_pile_up_workers(self, caplog):
        grabber = _Grabber()
        release = threading.Event()
        abandoned = threading.Thread(target=release.wait)
        abandoned.start()
        grabber._abandoned_detection_thread = abandoned
        grabber._detection_generation = 4
        try:
            with caplog.at_level("WARNING"):
                grabber._restart_stalled_detection(80)
            assert grabber._detection_generation == 4
            assert grabber.detection_thread is None
            assert "previous worker has not exited" in caplog.text
        finally:
            release.set()
            abandoned.join(1.0)


class TestHealthStaleness:
    def test_snapshot_marks_only_the_stalled_camera(self):
        now = 1_000_000.0
        stale = SimpleNamespace(
            detection_enabled=True,
            running=True,
            last_detection_ts=now - 90,
            _detection_worker_started_ts=now - 200,
            _last_successful_read=now,
            _last_detection_enqueue_ts=now,
            _last_detection_restart_ts=0.0,
            detection_queue=[1],
        )
        fresh = SimpleNamespace(
            detection_enabled=True,
            running=True,
            last_detection_ts=now - 1,
            _detection_worker_started_ts=now - 200,
            _last_successful_read=now,
            _last_detection_enqueue_ts=now,
            _last_detection_restart_ts=0.0,
            detection_queue=[],
        )
        report = detection_health({0: stale, 3: fresh}, now=now)
        assert report["stale"] is True
        assert report["last_detection_age_s"] >= 90
        by_id = {row["camera_id"]: row for row in report["cameras"]}
        assert by_id[0]["stale"] is True
        assert by_id[0]["last_detection_ts"] == stale.last_detection_ts
        assert by_id[3]["stale"] is False

    def test_health_endpoint_reports_staleness(self, monkeypatch):
        now = time.time()
        stale = SimpleNamespace(
            detection_enabled=True,
            running=True,
            last_detection_ts=now - 90,
            _detection_worker_started_ts=now - 200,
            _last_successful_read=now,
            _last_detection_enqueue_ts=now,
            _last_detection_restart_ts=0.0,
            detection_queue=[1],
        )
        monkeypatch.setattr("basebuddy.modules.state.grabbers", {9: stale})
        app = Flask(__name__)
        app.register_blueprint(health_api)
        with app.test_client() as client:
            resp = client.get("/health")
        body = resp.get_json()
        assert resp.status_code == 503
        assert body["ok"] is False
        assert body["detection"]["stale"] is True
        assert body["detection"]["last_detection_age_s"] >= 45
        assert body["detection"]["cameras"][0]["camera_id"] == 9
        assert body["detection"]["cameras"][0]["last_detection_ts"] is not None

    def test_health_endpoint_stays_ok_when_detection_is_fresh(self, monkeypatch):
        now = time.time()
        fresh = SimpleNamespace(
            detection_enabled=True,
            running=True,
            last_detection_ts=now,
            _detection_worker_started_ts=now - 200,
            _last_successful_read=now,
            _last_detection_enqueue_ts=now,
            _last_detection_restart_ts=0.0,
            detection_queue=[],
        )
        monkeypatch.setattr("basebuddy.modules.state.grabbers", {0: fresh})
        app = Flask(__name__)
        app.register_blueprint(health_api)
        with app.test_client() as client:
            resp = client.get("/health")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["ok"] is True
        assert body["detection"]["stale"] is False
        assert body["detection"]["last_detection_age_s"] < 5
