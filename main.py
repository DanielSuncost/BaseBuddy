#!/usr/bin/env python3
"""Repository entry point — configures paths and runs the application."""
import os
import runpy
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.join(_REPO_ROOT, "basebuddy")

os.environ.setdefault("BASEBUDDY_REPO_ROOT", _REPO_ROOT)
os.environ.setdefault("BASEBUDDY_APP_ROOT", _APP_ROOT)

# The application imports itself as the `basebuddy` package, so the repo root
# (its parent) must be importable.
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

runpy.run_module("basebuddy.main", run_name="__main__")
