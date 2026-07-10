"""Tests for notification rule matching, cooldowns, and channel parsing."""
import time

import pytest

import basebuddy.core.services.notification_rules as nr


class FakeDB:
    def __init__(self, rules):
        self.rules = rules

    def list_notification_rules(self, camera_id=None, enabled_only=False):
        return self.rules


@pytest.fixture(autouse=True)
def _reset_cooldowns():
    nr._last_fired.clear()
    yield
    nr._last_fired.clear()


def _use_rules(monkeypatch, rules):
    monkeypatch.setattr(nr, "_db", lambda: FakeDB(rules))


class TestMatchRules:
    def test_wildcard_rule_matches_any_class(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "class_name": "*", "notify_on": "start"}])
        assert len(nr.match_rules(0, "person", 0.9, "start")) == 1
        assert len(nr.match_rules(0, "car", 0.9, "start")) == 1

    def test_class_filter(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "class_name": "person", "notify_on": "start"}])
        assert len(nr.match_rules(0, "person", 0.9, "start")) == 1
        assert len(nr.match_rules(0, "car", 0.9, "start")) == 0

    def test_camera_filter(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "camera_id": 2, "notify_on": "start"}])
        assert len(nr.match_rules(2, "person", 0.9, "start")) == 1
        assert len(nr.match_rules(3, "person", 0.9, "start")) == 0

    def test_min_confidence(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "min_confidence": 0.8, "notify_on": "start"}])
        assert len(nr.match_rules(0, "person", 0.9, "start")) == 1
        assert len(nr.match_rules(0, "person", 0.5, "start")) == 0

    def test_phase_filtering(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "notify_on": "end"}])
        assert len(nr.match_rules(0, "person", 0.9, "start")) == 0
        assert len(nr.match_rules(0, "person", 0.9, "end")) == 1

    def test_both_matches_start_and_end(self, monkeypatch):
        _use_rules(monkeypatch, [{"id": 1, "notify_on": "both"}])
        assert len(nr.match_rules(0, "person", 0.9, "start")) == 1
        assert len(nr.match_rules(0, "person", 0.9, "end")) == 1


class TestCooldown:
    def test_first_fire_allowed_then_blocked(self):
        assert nr.cooldown_ok(1, 0, "person", cooldown_s=60)
        assert not nr.cooldown_ok(1, 0, "person", cooldown_s=60)

    def test_cooldown_expires(self):
        assert nr.cooldown_ok(1, 0, "person", cooldown_s=0.01)
        time.sleep(0.02)
        assert nr.cooldown_ok(1, 0, "person", cooldown_s=0.01)

    def test_cooldowns_are_scoped_per_rule_camera_class(self):
        assert nr.cooldown_ok(1, 0, "person", cooldown_s=60)
        assert nr.cooldown_ok(2, 0, "person", cooldown_s=60)
        assert nr.cooldown_ok(1, 1, "person", cooldown_s=60)
        assert nr.cooldown_ok(1, 0, "car", cooldown_s=60)


class TestParseChannels:
    def test_list_input(self):
        assert nr.parse_channels(["Telegram", " email "]) == ["telegram", "email"]

    def test_json_string(self):
        assert nr.parse_channels('["telegram", "sms"]') == ["telegram", "sms"]

    def test_csv_string(self):
        assert nr.parse_channels("telegram, email") == ["telegram", "email"]

    def test_garbage_input(self):
        assert nr.parse_channels(None) == []
        assert nr.parse_channels(42) == []
        assert nr.parse_channels("") == []
