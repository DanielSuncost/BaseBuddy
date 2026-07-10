"""Tests for the reconstruction engine registry."""
from basebuddy.modules.multiview.engines import list_engines, resolve_engine
from basebuddy.modules.multiview.engines.sfm_engine import SfMEngine


def test_registry_lists_all_engines():
    entries = list_engines()
    ids = [e['id'] for e in entries]
    assert ids[0] == 'auto'
    for expected in ('vggt', 'pi3', 'dust3r', 'sfm'):
        assert expected in ids
    for e in entries:
        assert set(e) >= {'id', 'label', 'available', 'description', 'recommended'}


def test_sfm_always_available():
    assert SfMEngine().available() is True
    assert resolve_engine('sfm') is not None


def test_auto_resolves_to_something():
    # SfM has no ML deps, so auto can never come back empty.
    eng = resolve_engine('auto')
    assert eng is not None


def test_unknown_engine_returns_none():
    assert resolve_engine('colmap') is None
