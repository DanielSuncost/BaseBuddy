"""Build a compact animated GIF of SAM mask evolution for a growth series.

Uses Pillow (already a dependency). Pure helpers are easy to unit-test with
synthetic arrays; I/O is isolated in ``write_mask_gif`` / ``ensure_series_mask_gif``.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_MAX_FRAMES = 48
DEFAULT_RECENT_S = 72 * 3600
DEFAULT_MAX_SIDE = 480
DEFAULT_DURATION_MS = 350
# Match plant color metrics — skip pitch-black / grow-light wash frames.
_L_FRAME_LOW = 0.08
_PLANT_S_MIN = 12.0
# Teal tint — matches BaseBuddy plant / SAM chart accent (not purple).
OVERLAY_TINT_BGR = (180, 140, 20)  # soft teal in BGR
SILHOUETTE_FG_BGR = (255, 255, 255)
SILHOUETTE_BG_BGR = (0, 0, 0)


def _parse_flags(sample: Dict[str, Any]) -> List[str]:
    flags = sample.get("quality_flags") or []
    if isinstance(flags, str):
        try:
            import json

            parsed = json.loads(flags)
            flags = parsed if isinstance(parsed, list) else []
        except Exception:
            flags = []
    return [str(f) for f in flags]


def sample_is_low_light(sample: Dict[str, Any]) -> bool:
    """True when a growth/color sample should be excluded from mask GIFs."""
    flags = _parse_flags(sample)
    if any(
        f in flags
        for f in (
            "L_bg_low",
            "L_frame_low",
            "L_plant_low",
            "plant_desaturated",
            "G_norm_dim_light",
        )
    ):
        return True
    for key in ("L_frame", "L_bg", "L_plant"):
        v = sample.get(key)
        if v is not None:
            try:
                if float(v) < _L_FRAME_LOW:
                    return True
            except (TypeError, ValueError):
                pass
    metrics = sample.get("metrics") or {}
    if isinstance(metrics, dict):
        for key in ("L_frame", "L_bg", "L_plant"):
            v = metrics.get(key)
            if v is not None:
                try:
                    if float(v) < _L_FRAME_LOW:
                        return True
                except (TypeError, ValueError):
                    pass
    return False


def frame_mean_luminance(frame_bgr: np.ndarray) -> float:
    """Mean HSV V of a BGR frame in 0–1."""
    if frame_bgr is None or frame_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 2]) / 255.0)


def frame_is_low_light(frame_bgr: np.ndarray) -> bool:
    return frame_mean_luminance(frame_bgr) < _L_FRAME_LOW


def growth_series_dir(camera_id: int, series_id: int) -> str:
    from basebuddy.plugins.plant_health.segmentation import camera_key

    cam = camera_key(camera_id)
    return os.path.join("stills", cam, "sam_growth", f"series_{series_id}")


def mask_gif_path(camera_id: int, series_id: int) -> str:
    return os.path.join(growth_series_dir(camera_id, series_id), "mask_evolution.gif")


def render_mask_frame(
    mask: np.ndarray,
    frame_bgr: Optional[np.ndarray] = None,
    *,
    mode: str = "overlay",
    max_side: int = DEFAULT_MAX_SIDE,
    label: Optional[str] = None,
) -> np.ndarray:
    """Return a BGR uint8 visualization of *mask* (optionally over *frame_bgr*)."""
    if mask is None:
        raise ValueError("mask required")
    plant = mask > 0
    if frame_bgr is not None and mode == "overlay":
        h, w = frame_bgr.shape[:2]
        if mask.shape[0] != h or mask.shape[1] != w:
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
            plant = mask > 0
        out = frame_bgr.copy()
        tint = np.zeros_like(out)
        tint[:] = OVERLAY_TINT_BGR
        blend = cv2.addWeighted(out, 0.55, tint, 0.45, 0)
        out[plant] = blend[plant]
        # Soft silhouette edge
        edges = cv2.Canny((plant.astype(np.uint8) * 255), 40, 120)
        out[edges > 0] = (40, 220, 180)
    else:
        h, w = plant.shape[:2]
        out = np.full((h, w, 3), SILHOUETTE_BG_BGR, dtype=np.uint8)
        out[plant] = SILHOUETTE_FG_BGR

    out = _downscale(out, max_side)
    if label and mode != "silhouette":
        cv2.putText(
            out,
            label,
            (8, max(18, out.shape[0] - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (245, 245, 245),
            1,
            cv2.LINE_AA,
        )
    return out


def extract_mask_from_overlay(img_bgr: np.ndarray) -> np.ndarray:
    """Recover a binary plant mask from a teal-tinted overlay still."""
    if img_bgr is None or img_bgr.size == 0:
        return np.zeros((1, 1), dtype=np.uint8)
    b, g, r = cv2.split(img_bgr)
    b16 = b.astype(np.int16)
    r16 = r.astype(np.int16)
    # Overlay blends plant pixels toward teal BGR (180, 140, 20).
    teal = (b16 - r16 > 28) & (b > 60)
    # Cyan Canny edge stamped on overlays (40, 220, 180).
    cyan = (g > 150) & (b < 90) & (r > 100)
    mask = np.zeros(b.shape, dtype=np.uint8)
    mask[teal | cyan] = 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n_labels <= 1:
        return mask
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(labels == largest, 255, 0).astype(np.uint8)


def silhouette_path_for_overlay(overlay_path: str) -> str:
    directory, name = os.path.split(overlay_path)
    if name.startswith("mask_"):
        return os.path.join(directory, "sil_" + name[5:])
    stem, ext = os.path.splitext(name)
    return os.path.join(directory, f"{stem}_sil{ext or '.jpg'}")


def write_white_silhouette(mask: np.ndarray, out_path: str, *, max_side: int = DEFAULT_MAX_SIDE) -> str:
    viz = render_mask_frame(mask, None, mode="silhouette", max_side=max_side, label=None)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, viz, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    return out_path


def ensure_silhouette_from_overlay(
    overlay_path: str,
    *,
    force: bool = False,
    max_side: int = DEFAULT_MAX_SIDE,
) -> Optional[str]:
    """Build a white-on-black silhouette JPEG next to an overlay still."""
    if not overlay_path or not os.path.isfile(overlay_path):
        return None
    out_path = silhouette_path_for_overlay(overlay_path)
    if not force and os.path.isfile(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(overlay_path) - 1:
        return out_path
    img = cv2.imread(overlay_path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    mask = extract_mask_from_overlay(img)
    if int(np.sum(mask > 0)) < 40:
        return None
    return write_white_silhouette(mask, out_path, max_side=max_side)


def _downscale(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    side = max(h, w)
    if side <= max_side or max_side <= 0:
        return img
    scale = max_side / float(side)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


def select_mask_gif_samples(
    samples: Sequence[Dict[str, Any]],
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    recent_s: float = DEFAULT_RECENT_S,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Pick samples that have a mask_path, capped for a small GIF.

    Strategy: keep *all* samples in the recent window; for older history keep
    at most one per UTC day; then uniformly thin if still over *max_frames*.
    """
    usable = [
        s
        for s in (samples or [])
        if s.get("ok", True)
        and s.get("mask_path")
        and s.get("sampled_at") is not None
        and not sample_is_low_light(s)
    ]
    if not usable:
        return []
    usable = sorted(usable, key=lambda s: float(s["sampled_at"]))
    now_ts = float(now if now is not None else time.time())
    recent_cut = now_ts - float(recent_s)

    recent = [s for s in usable if float(s["sampled_at"]) >= recent_cut]
    older = [s for s in usable if float(s["sampled_at"]) < recent_cut]

    # One per UTC day for older history (keep last of each day)
    by_day: Dict[str, Dict[str, Any]] = {}
    for s in older:
        day = datetime.fromtimestamp(float(s["sampled_at"]), tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        prev = by_day.get(day)
        if prev is None or float(s["sampled_at"]) >= float(prev["sampled_at"]):
            by_day[day] = s
    older_daily = [by_day[k] for k in sorted(by_day.keys())]

    chosen = older_daily + recent
    if len(chosen) <= max_frames:
        return chosen
    # Uniform thin while preserving first + last
    if max_frames <= 2:
        return [chosen[0], chosen[-1]] if len(chosen) >= 2 else chosen[:max_frames]
    idxs = np.linspace(0, len(chosen) - 1, num=max_frames)
    picked = []
    seen = set()
    for i in idxs:
        ii = int(round(i))
        if ii in seen:
            continue
        seen.add(ii)
        picked.append(chosen[ii])
    return picked


def frames_bgr_to_gif_bytes(
    frames_bgr: Sequence[np.ndarray],
    *,
    duration_ms: int = DEFAULT_DURATION_MS,
) -> bytes:
    """Encode BGR frames as an animated GIF (Pillow). Returns raw bytes."""
    from io import BytesIO

    from PIL import Image

    if not frames_bgr:
        raise ValueError("Need at least one frame")
    pil_frames = []
    target_size = None
    for fr in frames_bgr:
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        im = Image.fromarray(rgb)
        if target_size is None:
            target_size = im.size
        elif im.size != target_size:
            im = im.resize(target_size, Image.Resampling.BILINEAR)
        # Quantize for smaller GIF
        pil_frames.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
    buf = BytesIO()
    pil_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pil_frames[1:],
        duration=max(50, int(duration_ms)),
        loop=0,
        optimize=True,
    )
    return buf.getvalue()


def write_mask_gif(
    frames_bgr: Sequence[np.ndarray],
    out_path: str,
    *,
    duration_ms: int = DEFAULT_DURATION_MS,
) -> str:
    data = frames_bgr_to_gif_bytes(frames_bgr, duration_ms=duration_ms)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def load_mask_viz_frame(
    sample: Dict[str, Any],
    *,
    max_side: int = DEFAULT_MAX_SIDE,
) -> Optional[np.ndarray]:
    """Load a stored mask_path (viz JPEG/PNG or binary mask) as a BGR frame."""
    path = sample.get("mask_path")
    if not path or not os.path.isfile(path):
        return None
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    label = None
    ts = sample.get("sampled_at")
    if ts is not None:
        label = datetime.fromtimestamp(float(ts)).strftime("%b %d %H:%M")

    if len(img.shape) == 2 or (len(img.shape) == 3 and img.shape[2] == 1):
        # Binary / grayscale mask → silhouette with label
        mask = img.squeeze()
        return render_mask_frame(mask, None, mode="silhouette", max_side=max_side, label=label)

    # Already a color viz — downscale + stamp label
    out = _downscale(img, max_side)
    if label:
        cv2.putText(
            out,
            label,
            (8, max(18, out.shape[0] - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (245, 245, 245),
            1,
            cv2.LINE_AA,
        )
    return out


def build_gif_from_samples(
    samples: Sequence[Dict[str, Any]],
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    recent_s: float = DEFAULT_RECENT_S,
    max_side: int = DEFAULT_MAX_SIDE,
    duration_ms: int = DEFAULT_DURATION_MS,
    now: Optional[float] = None,
) -> Tuple[Optional[bytes], List[Dict[str, Any]], Optional[str]]:
    """Select + render frames; return (gif_bytes, selected_samples, error)."""
    selected = select_mask_gif_samples(
        samples, max_frames=max_frames, recent_s=recent_s, now=now
    )
    if len(selected) < 1:
        return None, [], "No mask stills yet — run growth samples first"
    frames = []
    kept = []
    for s in selected:
        fr = load_mask_viz_frame(s, max_side=max_side)
        if fr is None:
            continue
        # Skip dark stills even when metadata lacked L_* (legacy growth rows).
        if frame_is_low_light(fr):
            continue
        frames.append(fr)
        kept.append(s)
    if len(frames) < 1:
        return None, selected, "No usable (non-dark) mask stills"
    try:
        data = frames_bgr_to_gif_bytes(frames, duration_ms=duration_ms)
    except Exception as exc:
        logger.warning("GIF encode failed: %s", exc)
        return None, kept, str(exc)
    return data, kept, None


def ensure_series_mask_gif(
    series_id: int,
    camera_id: int,
    *,
    force: bool = False,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> Dict[str, Any]:
    """Build/refresh the on-disk GIF for a series. Soft-fail friendly."""
    from basebuddy.plugins.plant_health.sam_series import list_growth_samples

    out_path = mask_gif_path(camera_id, series_id)
    samples = list_growth_samples(series_id, limit=2000)
    with_mask = [s for s in samples if s.get("mask_path")]
    if len(with_mask) < 1:
        return {"ok": False, "error": "No mask stills", "path": None, "frame_count": 0}

    # Skip rebuild if GIF is newer than newest mask still (unless forced)
    newest = max(float(s["sampled_at"]) for s in with_mask)
    if (
        not force
        and os.path.isfile(out_path)
        and os.path.getmtime(out_path) >= newest - 1
    ):
        return {
            "ok": True,
            "path": out_path,
            "cached": True,
            "frame_count": None,
            "url": "/" + out_path.replace("\\", "/"),
        }

    data, selected, err = build_gif_from_samples(with_mask, max_frames=max_frames)
    if err or not data:
        return {"ok": False, "error": err or "encode failed", "path": None, "frame_count": 0}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return {
        "ok": True,
        "path": out_path,
        "cached": False,
        "frame_count": len(selected),
        "bytes": len(data),
        "url": "/" + out_path.replace("\\", "/"),
    }
