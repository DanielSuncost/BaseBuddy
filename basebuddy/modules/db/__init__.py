"""AnalyticsDB domain mixins."""
from basebuddy.modules.db.events_write import DetectionEventsMixin
from basebuddy.modules.db.queries import DetectionQueriesMixin
from basebuddy.modules.db.traffic import TrafficMixin
from basebuddy.modules.db.event_sessions import EventSessionsMixin
from basebuddy.modules.db.notification_rules import NotificationRulesMixin
from basebuddy.modules.db.person_reid import PersonReIDMixin

__all__ = [
    "DetectionEventsMixin",
    "DetectionQueriesMixin",
    "TrafficMixin",
    "EventSessionsMixin",
    "NotificationRulesMixin",
    "PersonReIDMixin",
]
