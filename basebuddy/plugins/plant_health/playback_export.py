"""Compose a compact playback board (stills + calendar + plots) and export GIF/MOV."""
from __future__ import annotations

import calendar as calmod
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from basebuddy.plugins.plant_health.mask_gif import (
    ensure_silhouette_from_overlay,
    write_mask_gif,
)

logger = logging.getLogger(__name__)

BOARD_W = 1280
BOARD_H = 720
NIGHT_WEIGHT = 0.07
BASE_DURATION_MS = 420
DEFAULT_GIF_FRAMES = 72
DEFAULT_MOV_FRAMES = 160

_BG = (18, 16, 11)  # BGR #0b1012-ish
_PANE = (28, 22, 15)
_TEAL = (180, 184, 20)
_TEAL_LINE = (110, 118, 15)
_GREEN_LINE = (74, 163, 22)
_BLUE_LINE = (199, 132, 2)
_PLAYHEAD = (11, 158, 245)
_MUTED = (160, 148, 130)
_TEXT = (248, 250, 252)
_GRID = (70, 62, 52)


def is_lights_out(sample: Optional[Dict[str, Any]]) -> bool:
    if not sample:
        return False
    if sample.get("lights_out"):
        return True
    flags = sample.get("quality_flags") or []
    if isinstance(flags, str):
        try:
            import json

            parsed = json.loads(flags)
            flags = parsed if isinstance(parsed, list) else []
        except Exception:
            flags = []
    flags = [str(f) for f in flags]
    if "plant_desaturated" in flags:
        return True
    green_zero = sample.get("green_pixels_in_mask") == 0 or sample.get("green_fraction_in_mask") == 0
    if "low_green_in_mask" in flags and green_zero:
        return True
    if sample.get("L_plant") is not None:
        try:
            if float(sample["L_plant"]) > 0.7 and green_zero:
                return True
        except (TypeError, ValueError):
            pass
    return False


def resolve_still(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    text = str(path).replace("\\", "/")
    if text.startswith("http://") or text.startswith("https://"):
        return None
    if text.startswith("/stills/"):
        text = text[1:]
    elif text.startswith("/"):
        text = text.lstrip("/")
    if os.path.isfile(path):
        return path
    if os.path.isfile(text):
        return text
    try:
        from basebuddy.core.paths import get_repo_root

        cand = os.path.join(get_repo_root(), text)
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


def load_bgr(path: Optional[str]) -> Optional[np.ndarray]:
    resolved = resolve_still(path)
    if not resolved:
        return None
    img = cv2.imread(resolved, cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


def attach_compressed_x(samples: Sequence[Dict[str, Any]]) -> float:
    x = 0.0
    rows = list(samples or [])
    for i, s in enumerate(rows):
        if i == 0:
            s["_cx"] = 0.0
            continue
        dt = max(0.0, float(s.get("sampled_at") or 0) - float(rows[i - 1].get("sampled_at") or 0))
        night = is_lights_out(s) or is_lights_out(rows[i - 1])
        x += dt * NIGHT_WEIGHT if night else dt
        s["_cx"] = x
    return max(x, 1.0)


def duration_ms_for_speed(speed: float) -> int:
    spd = max(0.25, float(speed or 1.0))
    return max(40, int(round(BASE_DURATION_MS / spd)))


def select_export_indices(n: int, max_frames: int) -> List[int]:
    if n <= 0:
        return []
    cap = max(2, int(max_frames or n))
    if n <= cap:
        return list(range(n))
    idxs = np.linspace(0, n - 1, num=cap)
    out: List[int] = []
    seen = set()
    for i in idxs:
        ii = int(round(float(i)))
        if ii in seen:
            continue
        seen.add(ii)
        out.append(ii)
    if 0 not in seen:
        out.insert(0, 0)
    if n - 1 not in seen:
        out.append(n - 1)
    return sorted(set(out))


def _put_text(img: np.ndarray, text: str, org: Tuple[int, int], *, scale=0.48, color=_TEXT, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def letterbox(src: np.ndarray, w: int, h: int, bg: Tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    out = np.full((h, w, 3), bg, dtype=np.uint8)
    if src is None or src.size == 0:
        return out
    sh, sw = src.shape[:2]
    scale = min(w / max(1, sw), h / max(1, sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(src, (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = (w - nw) // 2
    y0 = (h - nh) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = resized
    return out


def draw_mini_calendar(
    img: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    frames: Sequence[Dict[str, Any]],
    idx: int,
) -> None:
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (40, 32, 24), -1)
    if not frames:
        return
    cur = frames[max(0, min(idx, len(frames) - 1))]
    d = datetime.fromtimestamp(float(cur.get("sampled_at") or 0))
    year, month = d.year, d.month
    _put_text(img, d.strftime("%b %Y"), (x + 6, y + 14), scale=0.36, color=_MUTED)

    first_wd, days_in_month = calmod.monthrange(year, month)
    # Sunday-first like the UI
    start_pad = (first_wd + 1) % 7
    played = set()
    has = set()
    for i, f in enumerate(frames):
        fd = datetime.fromtimestamp(float(f.get("sampled_at") or 0))
        if fd.year != year or fd.month != month:
            continue
        has.add(fd.day)
        if i <= idx:
            played.add(fd.day)

    grid_y = y + 20
    grid_h = h - 24
    cell_w = max(4, (w - 8) // 7)
    cell_h = max(4, grid_h // 7)
    for col, lab in enumerate("SMTWTFS"):
        _put_text(img, lab, (x + 5 + col * cell_w, grid_y + 8), scale=0.26, color=(110, 100, 90))
    day = 1
    for row in range(6):
        for col in range(7):
            slot = row * 7 + col
            if slot < start_pad or day > days_in_month:
                continue
            cx = x + 4 + col * cell_w
            cy = grid_y + 12 + row * cell_h
            color = (48, 42, 36)
            if day in played:
                color = _TEAL
            elif day in has:
                color = (80, 90, 40)
            cv2.rectangle(img, (cx, cy), (cx + cell_w - 2, cy + cell_h - 2), color, -1)
            if day == d.day:
                cv2.rectangle(img, (cx, cy), (cx + cell_w - 2, cy + cell_h - 2), _PLAYHEAD, 1)
            day += 1


def _range_of(vals: Sequence[float]) -> Tuple[float, float]:
    ok = [float(v) for v in vals if v is not None]
    if not ok:
        return 0.0, 1.0
    mn, mx = min(ok), max(ok)
    pad = max(abs(mx - mn) * 0.14, abs(mx) * 0.03, 1.0)
    if mx - mn < 1e-9:
        return mn - pad, mx + pad
    return mn - pad * 0.12, mx + pad * 0.18


def draw_line_chart(
    img: np.ndarray,
    x0: int,
    y0: int,
    w: int,
    h: int,
    samples: Sequence[Dict[str, Any]],
    key: str,
    *,
    until: Optional[float],
    color: Tuple[int, int, int],
    title: str,
    format_y=None,
) -> None:
    format_y = format_y or (lambda v: str(int(round(v))))
    cv2.rectangle(img, (x0, y0), (x0 + w - 1, y0 + h - 1), _PANE, -1)
    _put_text(img, title, (x0 + 8, y0 + 16), scale=0.40, color=_TEXT, thick=1)

    usable = [
        s
        for s in (samples or [])
        if s.get("ok", True) is not False and s.get(key) is not None
    ]
    if until is not None:
        usable = [s for s in usable if float(s.get("sampled_at") or 0) <= float(until)]
    axis = usable or list(samples or [])
    cmax = attach_compressed_x(axis)
    day_pts = [s for s in usable if not is_lights_out(s)]
    if not day_pts:
        _put_text(img, "No daytime samples", (x0 + 12, y0 + h // 2), scale=0.38, color=_MUTED)
        return

    pad_l, pad_r, pad_t, pad_b = 44, 8, 24, 16
    plot_w = max(8, w - pad_l - pad_r)
    plot_h = max(8, h - pad_t - pad_b)
    vals = [float(s[key]) for s in day_pts]
    vmin, vmax = _range_of(vals)

    def x_for(s):
        return int(x0 + pad_l + (float(s.get("_cx") or 0) / cmax) * plot_w)

    def y_for(v):
        t = (float(v) - vmin) / max(1e-9, vmax - vmin)
        return int(y0 + pad_t + plot_h - t * plot_h)

    night_start = None
    for i, s in enumerate(axis):
        night = is_lights_out(s)
        if night and night_start is None:
            night_start = s
        end_night = (not night or i == len(axis) - 1) and night_start
        if end_night:
            a = x_for(night_start)
            b = x_for(s if night else axis[max(0, i - 1)])
            left, right = min(a, b), max(a, b)
            cv2.rectangle(
                img,
                (left, y0 + pad_t),
                (max(left + 1, right), y0 + pad_t + plot_h),
                (22, 18, 14),
                -1,
            )
            night_start = None

    for i in range(5):
        gy = y0 + pad_t + int(plot_h * i / 4)
        cv2.line(img, (x0 + pad_l, gy), (x0 + pad_l + plot_w, gy), _GRID, 1)

    _put_text(img, format_y(vmax), (x0 + 4, y0 + pad_t + 4), scale=0.28, color=_MUTED)
    _put_text(img, format_y(vmin), (x0 + 4, y0 + pad_t + plot_h), scale=0.28, color=_MUTED)

    pts = [(x_for(s), y_for(s[key])) for s in day_pts]
    if len(pts) >= 2:
        cv2.polylines(img, [np.array(pts, dtype=np.int32)], False, color, 2, cv2.LINE_AA)
    for px, py in pts:
        cv2.circle(img, (px, py), 2, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(img, (px, py), 2, color, 1, cv2.LINE_AA)

    if until is not None:
        head = None
        for s in reversed(day_pts):
            if float(s.get("sampled_at") or 0) <= float(until):
                head = s
                break
        if head is not None:
            hx, hy = x_for(head), y_for(head[key])
            cv2.line(img, (hx, y0 + pad_t), (hx, y0 + pad_t + plot_h), _PLAYHEAD, 1, cv2.LINE_AA)
            cv2.circle(img, (hx, hy), 4, _PLAYHEAD, -1, cv2.LINE_AA)


def _fmt_area(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}k"
    return str(int(round(v)))


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def compose_board_frame(
    overlay: Optional[np.ndarray],
    silhouette: Optional[np.ndarray],
    samples: Sequence[Dict[str, Any]],
    frames: Sequence[Dict[str, Any]],
    idx: int,
    *,
    title: str = "",
    size: Tuple[int, int] = (BOARD_W, BOARD_H),
) -> np.ndarray:
    """One compact UI-like board: two stills, tiny calendar, three unfolding plots."""
    w, h = int(size[0]), int(size[1])
    board = np.full((h, w, 3), _BG, dtype=np.uint8)
    pad = max(8, int(round(w * 0.012)))
    gap = max(6, int(round(w * 0.009)))
    header_h = max(72, int(round(h * 0.13)))
    plots_w = max(140, int(round(w * 0.266)))
    idx = max(0, min(idx, max(0, len(frames) - 1)))
    cur = frames[idx] if frames else {}
    ts = cur.get("sampled_at")
    when = "—"
    if ts:
        try:
            when = datetime.fromtimestamp(float(ts)).strftime("%a %b %-d, %-I:%M %p")
        except ValueError:
            when = datetime.fromtimestamp(float(ts)).strftime("%a %b %d, %I:%M %p")

    _put_text(board, title or "Growth", (pad, pad + 22), scale=0.62, color=_TEXT, thick=2)
    _put_text(board, when, (pad, pad + 48), scale=0.48, color=_TEXT)
    if frames:
        pct = int(round((idx / max(1, len(frames) - 1)) * 100))
        _put_text(board, f"{idx + 1} / {len(frames)}  ·  {pct}%", (pad, pad + 72), scale=0.40, color=_MUTED)

    cal_w = max(72, min(118, int(round(w * 0.09))))
    cal_h = max(58, min(86, header_h - 8))
    draw_mini_calendar(board, w - pad - cal_w, pad, cal_w, cal_h, frames, idx)

    stage_y = pad + header_h
    stage_h = h - stage_y - pad
    imgs_w = w - pad * 2 - gap - plots_w
    img_w = (imgs_w - gap) // 2
    cap_h = 20
    img_h = stage_h

    def pane(x, src, caption):
        cv2.rectangle(board, (x, stage_y), (x + img_w - 1, stage_y + img_h - 1), (8, 6, 4), -1)
        fitted = letterbox(src if src is not None else np.zeros((8, 8, 3), np.uint8), img_w, img_h - cap_h, (0, 0, 0))
        board[stage_y : stage_y + img_h - cap_h, x : x + img_w] = fitted
        _put_text(board, caption, (x + 8, stage_y + img_h - 6), scale=0.36, color=_MUTED)

    pane(pad, overlay, "MASK ON IMAGE")
    pane(pad + img_w + gap, silhouette, "MASK SHAPE")

    px = w - pad - plots_w
    plot_h = (stage_h - 2 * gap) // 3
    specs = (
        ("mask_area_px", "Mask area", _TEAL_LINE, _fmt_area),
        ("green_pixels_in_mask", "Green in mask", _GREEN_LINE, _fmt_area),
        ("green_fraction_in_mask", "% green in mask", _BLUE_LINE, _fmt_pct),
    )
    for i, (key, label, color, fmt) in enumerate(specs):
        py = stage_y + i * (plot_h + gap)
        draw_line_chart(
            board, px, py, plots_w, plot_h, samples, key,
            until=ts, color=color, title=label, format_y=fmt,
        )
    return board


def playback_rows_from_samples(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for s in samples or []:
        if s.get("ok") is False or is_lights_out(s):
            continue
        overlay = s.get("mask_path") or s.get("overlay_path")
        if overlay and not resolve_still(overlay):
            overlay = None
        sil = s.get("silhouette_path")
        if sil and not resolve_still(sil):
            sil = None
        if overlay and not sil:
            try:
                sil = ensure_silhouette_from_overlay(resolve_still(overlay) or overlay)
            except Exception:
                sil = None
        if not overlay and not sil:
            continue
        rows.append({
            "sampled_at": s.get("sampled_at"),
            "overlay_path": overlay,
            "silhouette_path": sil,
            "mask_area_px": s.get("mask_area_px"),
            "green_pixels_in_mask": s.get("green_pixels_in_mask"),
            "green_fraction_in_mask": s.get("green_fraction_in_mask"),
        })
    return rows


def render_board_frames(
    samples: Sequence[Dict[str, Any]],
    *,
    title: str = "",
    max_frames: int = DEFAULT_GIF_FRAMES,
    size: Tuple[int, int] = (BOARD_W, BOARD_H),
) -> List[np.ndarray]:
    rows = playback_rows_from_samples(samples)
    if not rows:
        return []
    picks = select_export_indices(len(rows), max_frames)
    boards: List[np.ndarray] = []
    for i in picks:
        row = rows[i]
        overlay = load_bgr(row.get("overlay_path"))
        sil = load_bgr(row.get("silhouette_path"))
        boards.append(
            compose_board_frame(overlay, sil, samples, rows, i, title=title, size=size)
        )
    return boards


def write_gif_export(frames: Sequence[np.ndarray], out_path: str, *, duration_ms: int) -> str:
    return write_mask_gif(frames, out_path, duration_ms=duration_ms)


def write_mov_export(frames: Sequence[np.ndarray], out_path: str, *, fps: float) -> str:
    if not frames:
        raise ValueError("Need at least one frame")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to export MOV")
    h, w = frames[0].shape[:2]
    if w % 2:
        w -= 1
    if h % 2:
        h -= 1
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rate = max(0.5, float(fps))
    with tempfile.TemporaryDirectory(prefix="sam_mov_") as tmp:
        for i, fr in enumerate(frames):
            if fr.shape[0] != h or fr.shape[1] != w:
                fr = cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)
            cv2.imwrite(os.path.join(tmp, f"f{i:05d}.png"), fr)
        cmd = [
            "ffmpeg", "-y",
            "-framerate", f"{rate:.4f}",
            "-i", os.path.join(tmp, "f%05d.png"),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-preset", "veryfast",
            "-crf", "20",
            out_path,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=180,
        )
        if proc.returncode != 0 or not os.path.isfile(out_path):
            msg = (proc.stderr or b"").decode("utf-8", "replace")[-600:]
            raise RuntimeError(f"ffmpeg MOV encode failed: {msg}")
    return out_path


def build_playback_export(
    samples: Sequence[Dict[str, Any]],
    *,
    fmt: str = "gif",
    speed: float = 1.0,
    title: str = "",
    max_frames: Optional[int] = None,
    out_path: Optional[str] = None,
) -> Dict[str, Any]:
    fmt = (fmt or "gif").lower().strip()
    if fmt not in ("gif", "mov"):
        return {"ok": False, "error": "format must be gif or mov"}
    cap = int(max_frames or (DEFAULT_MOV_FRAMES if fmt == "mov" else DEFAULT_GIF_FRAMES))
    boards = render_board_frames(samples, title=title, max_frames=cap)
    if not boards:
        return {"ok": False, "error": "No daytime playback stills to export"}
    duration_ms = duration_ms_for_speed(speed)
    fps = 1000.0 / duration_ms
    suffix = ".gif" if fmt == "gif" else ".mov"
    if not out_path:
        fd, out_path = tempfile.mkstemp(prefix="sam_playback_", suffix=suffix)
        os.close(fd)
    try:
        if fmt == "gif":
            write_gif_export(boards, out_path, duration_ms=duration_ms)
        else:
            write_mov_export(boards, out_path, fps=fps)
    except Exception as exc:
        logger.warning("Playback export failed: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "path": out_path,
        "format": fmt,
        "frame_count": len(boards),
        "duration_ms": duration_ms,
        "bytes": os.path.getsize(out_path) if os.path.isfile(out_path) else 0,
    }
