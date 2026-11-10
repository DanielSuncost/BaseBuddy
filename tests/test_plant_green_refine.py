"""Carve non-green pixels out of a SAM blob."""
from __future__ import annotations

import numpy as np

from basebuddy.plugins.plant_health.green_refine import (
    clip_to_template,
    green_score_map,
    refine_mask,
    refine_mask_by_green,
    refine_mask_by_luma,
)


def test_green_score_leaf_higher_than_gray():
    img = np.zeros((20, 30, 3), dtype=np.uint8)
    img[:, :] = (180, 180, 180)  # gray BGR
    img[4:16, 8:22] = (40, 180, 40)  # green leaf
    score = green_score_map(img)
    assert score[10, 15] > 20
    assert score[1, 1] < 5


def test_refine_zero_keeps_sam_blob():
    frame = np.full((16, 16, 3), 80, dtype=np.uint8)
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[2:14, 2:14] = 255
    out = refine_mask_by_green(frame, mask, 0)
    assert int(np.sum(out > 0)) == int(np.sum(mask > 0))


def test_refine_drops_gray_keeps_green():
    frame = np.zeros((24, 24, 3), dtype=np.uint8)
    frame[:, :] = (200, 200, 200)
    frame[6:18, 6:18] = (30, 170, 40)
    mask = np.ones((24, 24), dtype=np.uint8) * 255
    out = refine_mask_by_green(frame, mask, 16)
    # Interior of the green square stays
    assert out[12, 12] > 0
    # Gray corner is removed
    assert out[1, 1] == 0
    assert int(np.sum(out > 0)) < int(np.sum(mask > 0))
    assert int(np.sum(out > 0)) > 40


def test_refine_skips_desaturated_ir_wash():
    frame = np.full((20, 20, 3), 80, dtype=np.uint8)  # gray
    mask = np.ones((20, 20), dtype=np.uint8) * 255
    out = refine_mask_by_green(frame, mask, 20)
    assert int(np.sum(out > 0)) == 400


def test_refine_low_threshold_keeps_more_than_integer_step():
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[:, :] = (90, 110, 70)  # weak green-ish
    frame[6:14, 6:14] = (40, 160, 40)
    mask = np.ones((20, 20), dtype=np.uint8) * 255
    light = refine_mask_by_green(frame, mask, 0.3)
    heavy = refine_mask_by_green(frame, mask, 1.0)
    assert int(np.sum(light > 0)) >= int(np.sum(heavy > 0))


def test_luma_carve_drops_white_keeps_dark():
    frame = np.zeros((24, 24, 3), dtype=np.uint8)
    frame[:, :] = (240, 240, 240)
    frame[8:16, 8:16] = (20, 90, 20)
    mask = np.ones((24, 24), dtype=np.uint8) * 255
    out = refine_mask_by_luma(frame, mask, 50)
    assert out[12, 12] > 0
    assert out[1, 1] == 0
    off = refine_mask_by_luma(frame, mask, 0)
    assert int(np.sum(off > 0)) == 24 * 24


def test_refine_mask_combines_green_and_luma():
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[:, :] = (230, 230, 230)
    frame[4:10, 4:16] = (40, 180, 40)
    frame[12:18, 4:16] = (200, 220, 200)
    mask = np.ones((20, 20), dtype=np.uint8) * 255
    out = refine_mask(frame, mask, 8, 70)
    assert out[7, 10] > 0
    assert out[1, 1] == 0


def test_clip_to_template_never_grows():
    template = np.zeros((16, 16), dtype=np.uint8)
    template[4:12, 4:12] = 255
    grown = np.ones((16, 16), dtype=np.uint8) * 255
    out = clip_to_template(grown, template, bound_px=0)
    assert int(np.sum(out > 0)) == int(np.sum(template > 0))
    assert out[1, 1] == 0
    assert out[8, 8] > 0


def test_refine_mask_stays_inside_template():
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    frame[:, :] = (40, 180, 40)
    template = np.zeros((16, 16), dtype=np.uint8)
    template[4:12, 4:12] = 255
    # Incoming mask is already larger than the locked silhouette.
    huge = np.ones((16, 16), dtype=np.uint8) * 255
    out = refine_mask(frame, huge, 0, 0, template=template)
    assert out[1, 1] == 0
    assert out[8, 8] > 0


def test_growth_config_exposes_green_refine():
    from basebuddy.plugins.plant_health.sam_growth import growth_config

    cfg = growth_config({"sam_growth": {"green_refine": 22, "luma_carve": 80}})
    assert cfg["green_refine"] == 22
    assert cfg["luma_carve"] == 80
    cfg0 = growth_config({})
    assert cfg0["green_refine"] == 0
    assert cfg0["luma_carve"] == 0
