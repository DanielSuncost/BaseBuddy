#!/usr/bin/env python3
"""Import and wiring smoke test (no cameras required)."""
from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_ROOT = os.path.join(REPO_ROOT, "basebuddy")

os.environ.setdefault("BASEBUDDY_REPO_ROOT", REPO_ROOT)
os.environ.setdefault("BASEBUDDY_APP_ROOT", APP_ROOT)
os.environ.setdefault("DETECTION_ENABLED", "false")
os.environ.setdefault("HOME_SCENES_ENABLE", "false")
os.environ.setdefault("FLASK_ENV", "production")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

errors: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
        print(f"  OK  {label}")
    except Exception as exc:
        print(f"  FAIL {label}: {exc}")
        errors.append(label)


check("core.paths", lambda: __import__("basebuddy.core.paths", fromlist=["get_repo_root"]).get_repo_root())
check("core.inference", lambda: __import__("basebuddy.core.inference"))
check("inference router", lambda: __import__("basebuddy.core.inference.router", fromlist=["get_inference_router"]))
check("home_scenes config", lambda: __import__("basebuddy.plugins.home_scenes.config"))
check("cloud client", lambda: __import__("basebuddy.core.inference.cloud.client"))
check("training plugin", lambda: __import__("basebuddy.plugins.training.dataset_builder"))


def _health_check() -> None:
    from basebuddy.app import create_app

    app, _ = create_app("default")
    with app.test_client() as client:
        resp = client.get("/health")
        if resp.status_code != 200:
            raise RuntimeError(f"GET /health returned {resp.status_code}")


def _pages_check() -> None:
    """register_pages must succeed — no silent fallback to legacy web routes."""
    from basebuddy.app import create_app

    app, _ = create_app("default")
    rules = {r.rule for r in app.url_map.iter_rules()}
    for required in ("/", "/recordings", "/timelapse", "/gallery", "/config", "/metrics", "/training", "/traffic", "/people"):
        if required not in rules:
            raise RuntimeError(f"page route {required} is not registered")


check("app factory", lambda: __import__("basebuddy.app", fromlist=["create_app"]).create_app("default"))
check("/health endpoint", _health_check)
check("page routes registered", _pages_check)

if errors:
    print(f"\n{len(errors)} check(s) failed")
    sys.exit(1)
print("\nAll smoke checks passed")
