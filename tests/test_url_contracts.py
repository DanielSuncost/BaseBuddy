"""
JS <-> Flask URL contract test.

Scans every static JS file for fetch() calls with absolute URL literals and
asserts each one resolves to a registered Flask route. This catches the
"page saves to an endpoint that 404s" class of regression.
"""
import os
import re

import pytest

from basebuddy.core.paths import get_app_root

# URLs that intentionally have no OSS route. Add sparingly, with a reason.
ALLOWED_MISSING: set[str] = set()

_WILDCARD = "\x00*"  # marker for a JS template expression segment

_FETCH_RE = re.compile(r"""fetch\(\s*(['"`])(/[^'"`]*?)\1\s*([,)+])""")
_TEMPLATE_EXPR_RE = re.compile(r"\$\{[^}]*\}")


def _js_files():
    js_dir = os.path.join(get_app_root(), "static", "js")
    for root, _dirs, files in os.walk(js_dir):
        for name in files:
            if name.endswith(".js"):
                yield os.path.join(root, name)


def _extract_urls(source: str):
    """Yield (url_path, is_prefix) for fetch() calls with absolute URLs."""
    for match in _FETCH_RE.finditer(source):
        url = match.group(2)
        is_prefix = match.group(3) == "+"  # fetch('/api/foo/' + id ...)
        url = _TEMPLATE_EXPR_RE.sub(_WILDCARD, url)
        url = url.split("?", 1)[0]
        if url.startswith("/"):
            yield url, is_prefix


def _rule_segments(rule: str) -> list[str]:
    return [s for s in rule.split("/") if s != ""]


def _url_segments(url: str) -> list[str]:
    return [s for s in url.split("/") if s != ""]


def _seg_matches(js_seg: str, rule_seg: str, partial: bool = False) -> bool:
    if rule_seg.startswith("<"):
        return True
    if _WILDCARD in js_seg:
        # e.g. "run-${type}" — literal part must prefix the rule literal
        literal = js_seg.split(_WILDCARD, 1)[0]
        return rule_seg.startswith(literal)
    if partial:
        return rule_seg.startswith(js_seg)
    return js_seg == rule_seg


def _matches_rule(js_segs: list[str], rule_segs: list[str], is_prefix: bool) -> bool:
    for i, js_seg in enumerate(js_segs):
        if i >= len(rule_segs):
            return False
        rule_seg = rule_segs[i]
        if rule_seg.startswith("<path:"):
            return True  # path converter swallows the rest
        last = i == len(js_segs) - 1
        if not _seg_matches(js_seg, rule_seg, partial=is_prefix and last):
            return False
    if is_prefix:
        return True  # concatenation appends more segments
    return len(js_segs) == len(rule_segs)


@pytest.fixture(scope="module")
def route_rules():
    import basebuddy.modules.config as config

    # Optional plugins ship JS in this repo; include their routes
    config.HOME_SCENES_ENABLE = True

    from basebuddy.app import create_app

    app, _socketio = create_app()
    return [_rule_segments(r.rule) for r in app.url_map.iter_rules()]


def test_js_fetch_urls_resolve(route_rules):
    failures = []
    for js_path in _js_files():
        with open(js_path, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        rel = os.path.relpath(js_path, get_app_root())
        for url, is_prefix in set(_extract_urls(source)):
            display = url.replace(_WILDCARD, "${...}")
            if display in ALLOWED_MISSING:
                continue
            js_segs = _url_segments(url)
            if not js_segs:
                continue
            if not any(_matches_rule(js_segs, rule, is_prefix) for rule in route_rules):
                failures.append(f"{rel}: {display}")

    assert not failures, (
        "JS references URLs with no matching Flask route:\n  "
        + "\n  ".join(sorted(set(failures)))
    )
