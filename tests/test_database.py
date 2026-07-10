"""Tests for AnalyticsDB against a temporary SQLite database."""
import time

import pytest

from basebuddy.modules.database import AnalyticsDB


@pytest.fixture()
def db(tmp_path):
    return AnalyticsDB(db_path=str(tmp_path / "test.db"))


class TestEventSessions:
    def test_create_and_end_session(self, db):
        db.create_event_session(
            session_id="abc123",
            camera_id=1,
            class_name="person",
            track_id=7,
            confidence=0.85,
            snapshot_path="detections/x.jpg",
            region_labels=None,
            started_at=time.time(),
        )
        row = db.end_event_session("abc123", clip_path="recordings/clip.mp4")
        assert row is not None
        assert row["id"] == "abc123"
        assert row["camera_id"] == 1
        assert row["class_name"] == "person"
        assert row["clip_path"] == "recordings/clip.mp4"

    def test_update_keeps_max_confidence(self, db):
        db.create_event_session(
            session_id="s1", camera_id=0, class_name="car", track_id=1,
            confidence=0.9, snapshot_path=None, region_labels=None,
            started_at=time.time(),
        )
        db.update_event_session("s1", confidence=0.4, snapshot_path=None,
                                region_labels=None, updated_at=time.time())
        row = db.end_event_session("s1")
        assert row["max_confidence"] == pytest.approx(0.9)

    def test_update_raises_max_confidence(self, db):
        db.create_event_session(
            session_id="s2", camera_id=0, class_name="car", track_id=1,
            confidence=0.4, snapshot_path=None, region_labels=None,
            started_at=time.time(),
        )
        db.update_event_session("s2", confidence=0.95, snapshot_path=None,
                                region_labels=None, updated_at=time.time())
        row = db.end_event_session("s2")
        assert row["max_confidence"] == pytest.approx(0.95)

    def test_end_unknown_session_returns_none(self, db):
        assert db.end_event_session("missing") is None

    def test_list_event_sessions_filters(self, db):
        now = time.time()
        for i, cls in enumerate(["person", "car", "person"]):
            db.create_event_session(
                session_id=f"e{i}", camera_id=i % 2, class_name=cls, track_id=i,
                confidence=0.5, snapshot_path=None, region_labels=None,
                started_at=now,
            )
        assert len(db.list_event_sessions()) == 3
        assert len(db.list_event_sessions(class_name="person")) == 2
        assert len(db.list_event_sessions(camera_id=0)) == 2


class TestNotificationRules:
    def test_upsert_and_list(self, db):
        rule = db.upsert_notification_rule({
            "camera_id": 1,
            "class_name": "person",
            "min_confidence": 0.7,
            "channels": ["telegram", "email"],
            "notify_on": "start",
        })
        assert rule["id"]
        rules = db.list_notification_rules()
        assert len(rules) == 1
        assert rules[0]["channels"] == ["telegram", "email"]

    def test_update_existing_rule(self, db):
        rule = db.upsert_notification_rule({"class_name": "car", "channels": ["sms"]})
        updated = db.upsert_notification_rule({
            "id": rule["id"], "class_name": "car", "channels": ["email"],
        })
        assert updated["id"] == rule["id"]
        rules = db.list_notification_rules()
        assert len(rules) == 1
        assert rules[0]["channels"] == ["email"]

    def test_delete_rule(self, db):
        rule = db.upsert_notification_rule({"class_name": "dog", "channels": []})
        assert db.delete_notification_rule(rule["id"])
        assert db.list_notification_rules() == []
        assert not db.delete_notification_rule(99999)
