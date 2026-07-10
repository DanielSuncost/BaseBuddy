"""
Single source of truth for main navbar and Config sub-tabs.

Used by Jinja partials (base.html).
"""
from __future__ import annotations

from typing import List, TypedDict


class NavItem(TypedDict):
    id: str
    href: str
    icon: str
    label: str


# Main top navbar (left to right)
NAV_ITEMS: List[NavItem] = [
    {"id": "dashboard", "href": "/", "icon": "grid_view", "label": "Camera Wall"},
    {"id": "recordings", "href": "/recordings", "icon": "movie", "label": "Recordings"},
    {"id": "timelapse", "href": "/timelapse", "icon": "timelapse", "label": "Timelapse"},
    {"id": "multiview_3d", "href": "/multiview-3d", "icon": "view_in_ar", "label": "3D Multiview"},
    {"id": "gallery", "href": "/gallery", "icon": "photo_library", "label": "Gallery"},
    {"id": "people", "href": "/people", "icon": "face", "label": "Identities"},
    {"id": "training", "href": "/training", "icon": "model_training", "label": "Training"},
    {"id": "traffic", "href": "/traffic", "icon": "traffic", "label": "Traffic"},
    {"id": "events", "href": "/events", "icon": "history", "label": "Events"},
    {"id": "scenes", "href": "/scenes", "icon": "kitchen", "label": "Scenes"},
    {"id": "plants", "href": "/plants", "icon": "local_florist", "label": "Plants"},
    {"id": "resources", "href": "/resources", "icon": "memory", "label": "Resources"},
    {"id": "storage", "href": "/storage", "icon": "save", "label": "Storage"},
    {"id": "config", "href": "/config", "icon": "settings", "label": "Config"},
    {"id": "integrations", "href": "/integrations", "icon": "hub", "label": "Integrations"},
]

# Config section sub-tabs (shown when active_page == 'config' or active_config_tab is set)
CONFIG_TABS: List[NavItem] = [
    {"id": "setup", "href": "/config/setup", "icon": "rocket_launch", "label": "Getting Started"},
    {"id": "settings", "href": "/config", "icon": "settings", "label": "Settings"},
    {"id": "thresholds", "href": "/config/thresholds", "icon": "tune", "label": "Thresholds"},
    {"id": "tracking", "href": "/config/tracking", "icon": "timeline", "label": "Tracking"},
    {"id": "disabled-classes", "href": "/config/disabled-classes", "icon": "block", "label": "Disabled Classes"},
    {"id": "power", "href": "/config/power", "icon": "bolt", "label": "Power"},
]
