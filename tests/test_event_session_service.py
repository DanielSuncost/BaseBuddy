"""Tests for EventSessionService URL/payload helpers (no DB required)."""
from basebuddy.core.services.event_session_service import EventSessionService


class TestSnapshotUrl:
    def test_none_passthrough(self):
        assert EventSessionService._snapshot_url(None) is None

    def test_http_url_passthrough(self):
        url = "https://example.com/x.jpg"
        assert EventSessionService._snapshot_url(url) == url

    def test_relative_recordings_path(self):
        assert EventSessionService._snapshot_url("recordings/cam1/x.mp4") == "/recordings/cam1/x.mp4"

    def test_windows_separators_normalized(self):
        result = EventSessionService._snapshot_url("recordings\\cam1\\x.mp4")
        assert "\\" not in result
        assert result.startswith("/")

    def test_absolute_media_path(self):
        assert EventSessionService._snapshot_url("/media/detections/x.jpg") == "/media/detections/x.jpg"


class TestPayload:
    def test_payload_shape(self):
        svc = EventSessionService()
        payload = svc._payload(
            camera_id=0, class_name="person", session_id="abc",
            confidence=0.9, snapshot_path="detections/x.jpg", track_id=5,
        )
        assert payload["id"] == "abc"
        assert payload["camera"] == "camera_1"
        assert payload["label"] == "person"
        assert payload["track_id"] == 5
        assert "person detected on camera 1" in payload["message"]
        assert "(track 5)" in payload["message"]

    def test_payload_without_track(self):
        svc = EventSessionService()
        payload = svc._payload(
            camera_id=2, class_name="car", session_id="xyz",
            confidence=0.5, snapshot_path=None, track_id=None,
        )
        assert payload["camera"] == "camera_3"
        assert "track" not in payload["message"]
        assert payload["snapshot_url"] is None
