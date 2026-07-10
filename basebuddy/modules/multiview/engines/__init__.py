"""
Pluggable 3D reconstruction engines.

Engines take a list of BGR images and return a colored point cloud. Modern
feed-forward models (VGGT, Pi3) regress cameras + dense pointmaps for all
views in a single pass; DUSt3R (2024) is kept as a fallback, as is the
classical SIFT structure-from-motion pipeline which needs no ML deps at all.

Model weights are heavyweight (1-2.5 GB), so each engine caches its model as
a process-wide singleton behind a lock.
"""
from __future__ import annotations

from typing import List, Optional

from basebuddy.modules.multiview.engines.base import EngineResult, ReconstructionEngine
from basebuddy.modules.multiview.engines.vggt_engine import VGGTEngine
from basebuddy.modules.multiview.engines.pi3_engine import Pi3Engine
from basebuddy.modules.multiview.engines.dust3r_engine import Dust3REngine
from basebuddy.modules.multiview.engines.sfm_engine import SfMEngine

_ENGINES: List[ReconstructionEngine] = [
    VGGTEngine(),
    Pi3Engine(),
    Dust3REngine(),
    SfMEngine(),
]

# Preference order used by 'auto'.
_AUTO_ORDER = ('vggt', 'pi3', 'dust3r', 'sfm')


def list_engines() -> List[dict]:
    """Engine descriptors for the API, plus the 'auto' pseudo-engine."""
    resolved = resolve_engine('auto')
    entries = [{
        'id': 'auto',
        'label': 'Auto (best available)',
        'available': resolved is not None,
        'description': (f'Currently selects: {resolved.label}' if resolved
                        else 'No reconstruction engine available'),
        'recommended': True,
    }]
    for eng in _ENGINES:
        entries.append({
            'id': eng.id,
            'label': eng.label,
            'available': eng.available(),
            'description': eng.description,
            'recommended': False,
        })
    return entries


def resolve_engine(engine_id: str) -> Optional[ReconstructionEngine]:
    """Map an engine id ('auto' included) to an available engine instance."""
    if engine_id == 'auto':
        by_id = {e.id: e for e in _ENGINES}
        for eid in _AUTO_ORDER:
            if by_id[eid].available():
                return by_id[eid]
        return None
    for eng in _ENGINES:
        if eng.id == engine_id:
            return eng if eng.available() else None
    return None


__all__ = ['EngineResult', 'ReconstructionEngine', 'list_engines', 'resolve_engine']
