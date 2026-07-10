"""Tests for the event clip ring buffer: memory caps and session lifecycle."""
import numpy as np

import basebuddy.core.services.event_clip_buffer as ecb
from basebuddy.core.services.event_clip_buffer import EventClipBuffer, MAX_ACTIVE_SESSIONS


def _frame(w: int = 64, h: int = 48) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


class TestRingBuffer:
    def test_frames_are_stored_as_jpeg_bytes(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.push(_frame())
        assert len(buf._ring) == 1
        ts, payload = buf._ring[0]
        assert isinstance(payload, bytes)
        assert payload[:2] == b"\xff\xd8"  # JPEG magic

    def test_ring_is_bounded(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        maxlen = buf._ring.maxlen
        for _ in range(maxlen + 25):
            buf.push(_frame())
        assert len(buf._ring) == maxlen


class TestSessions:
    def test_session_starts_with_preroll(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        for _ in range(5):
            buf.push(_frame())
        buf.start_session("s1")
        assert len(buf._active["s1"].frames) == 5

    def test_session_receives_new_frames(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.start_session("s1")
        for _ in range(3):
            buf.push(_frame())
        assert len(buf._active["s1"].frames) == 3

    def test_session_frames_are_capped(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.start_session("s1")
        cap = buf._session_maxlen
        for _ in range(cap + 10):
            buf.push(_frame())
        assert len(buf._active["s1"].frames) == cap

    def test_active_session_count_is_capped(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        for i in range(MAX_ACTIVE_SESSIONS + 3):
            buf.start_session(f"s{i}")
        assert len(buf._active) == MAX_ACTIVE_SESSIONS

    def test_expired_sessions_are_dropped_on_push(self, monkeypatch):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.start_session("old")
        buf._active["old"].started_at -= ecb.MAX_SESSION_AGE_S + 1
        buf.push(_frame())
        assert "old" not in buf._active

    def test_drop_all_sessions(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.push(_frame())
        buf.start_session("a")
        buf.start_session("b")
        dropped = buf.drop_all_sessions()
        assert dropped == 2
        assert not buf._active
        assert not buf._ring

    def test_finalize_unknown_session_returns_none(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        assert buf.finalize_session("nope", post_seconds=0.0) is None

    def test_finalize_session_with_too_few_frames_returns_none(self):
        buf = EventClipBuffer(camera_id=0, pre_seconds=1.0, fps=10)
        buf.push(_frame())
        buf.start_session("s1")
        assert buf.finalize_session("s1", post_seconds=0.0) is None
        assert "s1" not in buf._active
