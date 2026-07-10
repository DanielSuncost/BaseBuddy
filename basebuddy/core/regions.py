"""
Camera regions — polygons/rects with labels, filter rules, analytics, and notifications.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

Region = Dict[str, Any]
Point = Tuple[float, float]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _normalize_point(p: Sequence[float]) -> Point:
    return (_clamp01(p[0]), _clamp01(p[1]))


def rect_to_points(x1: float, y1: float, x2: float, y2: float) -> List[Point]:
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [
        (_clamp01(x1), _clamp01(y1)),
        (_clamp01(x2), _clamp01(y1)),
        (_clamp01(x2), _clamp01(y2)),
        (_clamp01(x1), _clamp01(y2)),
    ]


def normalize_region(raw: dict) -> Region:
    """Normalize a region dict from API/storage."""
    region_id = raw.get('id') or f"r_{uuid.uuid4().hex[:10]}"
    label = (raw.get('label') or '').strip()

    points: List[Point] = []
    if raw.get('points'):
        for pt in raw['points']:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                points.append(_normalize_point(pt))
    elif all(k in raw for k in ('x1', 'y1', 'x2', 'y2')):
        points = rect_to_points(
            float(raw['x1']), float(raw['y1']),
            float(raw['x2']), float(raw['y2']),
        )

    if len(points) < 3:
        points = rect_to_points(0.0, 0.0, 1.0, 1.0)

    shape = raw.get('shape') or ('rect' if len(points) == 4 else 'polygon')
    if shape not in ('polygon', 'rect'):
        shape = 'polygon'

    filt = raw.get('filter') or raw.get('mode') or 'exclude'
    if filt not in ('include', 'exclude', 'none'):
        filt = 'exclude'

    notify_raw = raw.get('notify') if isinstance(raw.get('notify'), dict) else {}
    notify_classes = notify_raw.get('classes') or raw.get('notify_classes') or []
    if isinstance(notify_classes, str):
        notify_classes = [c.strip() for c in notify_classes.split(',') if c.strip()]

    tag = raw.get('tag_detections')
    if tag is None:
        tag = bool(label)

    analytics = raw.get('analytics')
    if analytics is None:
        analytics = bool(label)

    notify_enabled = notify_raw.get('enabled')
    if notify_enabled is None:
        notify_enabled = bool(notify_classes)

    return {
        'id': region_id,
        'label': label,
        'shape': shape,
        'points': [[p[0], p[1]] for p in points],
        'filter': filt,
        'tag_detections': bool(tag),
        'analytics': bool(analytics),
        'notify': {
            'enabled': bool(notify_enabled),
            'classes': list(notify_classes),
            'cooldown_s': int(notify_raw.get('cooldown_s') or raw.get('notify_cooldown_s') or 60),
        },
    }


def migrate_legacy_roi(raw: dict) -> Region:
    return normalize_region(raw)


def point_in_polygon(nx: float, ny: float, points: Sequence[Sequence[float]]) -> bool:
    """Ray-casting; nx/ny normalized 0–1."""
    n = len(points)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(points[i][0]), float(points[i][1])
        xj, yj = float(points[j][0]), float(points[j][1])
        if ((yi > ny) != (yj > ny)) and (
            nx < (xj - xi) * (ny - yi) / ((yj - yi) or 1e-9) + xi
        ):
            inside = not inside
        j = i
    return inside


def bbox_center_normalized(bbox, frame_shape) -> Point:
    h, w = frame_shape[0], frame_shape[1]
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0 / max(w, 1), (y1 + y2) / 2.0 / max(h, 1))


def center_in_region(bbox, region: Region, frame_shape) -> bool:
    cx, cy = bbox_center_normalized(bbox, frame_shape)
    return point_in_polygon(cx, cy, region.get('points') or [])


def load_camera_regions(cam_id: int) -> List[Region]:
    from basebuddy.modules.config import reload_ignored_rois
    raw_list = reload_ignored_rois().get(f'camera_{cam_id}', [])
    return [normalize_region(r) for r in raw_list if isinstance(r, dict)]


def regions_for_point(nx: float, ny: float, regions: Sequence[Region]) -> List[Region]:
    return [r for r in regions if point_in_polygon(nx, ny, r.get('points') or [])]


def regions_for_bbox(bbox, frame_shape, regions: Sequence[Region]) -> List[Region]:
    cx, cy = bbox_center_normalized(bbox, frame_shape)
    return regions_for_point(cx, cy, regions)


def should_filter_detection(bbox, frame_shape, regions: Sequence[Region]) -> bool:
    if not regions:
        return False
    include = [r for r in regions if r.get('filter') == 'include']
    exclude = [r for r in regions if r.get('filter') == 'exclude']
    matched = regions_for_bbox(bbox, frame_shape, regions)

    for r in exclude:
        if r in matched:
            return True
    if include:
        if not any(r in matched for r in include):
            return True
    return False


def tag_labels_for_bbox(bbox, frame_shape, regions: Sequence[Region]) -> List[str]:
    labels = []
    for r in regions_for_bbox(bbox, frame_shape, regions):
        if not r.get('tag_detections'):
            continue
        lab = (r.get('label') or '').strip()
        if lab:
            labels.append(lab)
    return sorted(set(labels))


def analytics_labels_for_bbox(bbox, frame_shape, regions: Sequence[Region]) -> List[str]:
    labels = []
    for r in regions_for_bbox(bbox, frame_shape, regions):
        if not r.get('analytics'):
            continue
        lab = (r.get('label') or '').strip()
        if lab:
            labels.append(lab)
    return sorted(set(labels))


def primary_analytics_label(bbox, frame_shape, regions: Sequence[Region]) -> Optional[str]:
    labs = analytics_labels_for_bbox(bbox, frame_shape, regions)
    return labs[0] if labs else None


def notify_regions_for_detection(
    bbox, class_name: str, frame_shape, regions: Sequence[Region]
) -> List[Region]:
    out = []
    for r in regions_for_bbox(bbox, frame_shape, regions):
        notify = r.get('notify') or {}
        if not notify.get('enabled'):
            continue
        classes = notify.get('classes') or []
        if classes and class_name not in classes:
            continue
        out.append(r)
    return out


def polygon_svg_points(region: Region) -> str:
    return ' '.join(f"{p[0]*100},{p[1]*100}" for p in region.get('points') or [])


def region_color(label: str, fallback: str = '#2563eb') -> str:
    if not label:
        return fallback
    h = sum(ord(c) for c in label) % 360
    return f'hsl({h}, 65%, 45%)'
