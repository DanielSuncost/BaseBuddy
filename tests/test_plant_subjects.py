"""Multi-angle plant grouping, alerts, and time-locked playback."""
from basebuddy.plugins.plant_health.subjects import (
    align_playback,
    build_agent_brief,
    combine_angles,
    group_monitors,
    nearest_frame,
    rename_updates,
)


def _angle(mid, *, rate=None, declining=False, g=0.8, coverage=0.2):
    return {
        "monitor_id": mid,
        "name": mid,
        "rate_per_day": rate,
        "declining": declining,
        "g_norm": g,
        "coverage": coverage,
    }


def test_group_keeps_solo_monitors_separate():
    groups = group_monitors([
        {"id": "a", "name": "A"},
        {"id": "b", "name": "B", "subject_id": "subject-1"},
        {"id": "c", "name": "C", "subject_id": "subject-1"},
    ])
    assert [len(g) for g in groups] == [1, 2]


def test_single_angle_alerts_on_its_own_decline():
    out = combine_angles([_angle("a", rate=-0.02, declining=True)])
    assert out["alert"] is True
    assert out["alert_basis"] == "single"
    assert out["split"] is False


def test_two_angles_alert_only_when_they_agree():
    both = combine_angles([
        _angle("a", rate=-0.02, declining=True),
        _angle("b", rate=-0.015, declining=True, g=0.7),
    ])
    assert both["alert"] is True
    assert both["alert_basis"] == "unanimous"
    assert both["split"] is False

    split = combine_angles([
        _angle("a", rate=-0.02, declining=True),
        _angle("b", rate=0.001, declining=False, g=0.9),
    ])
    assert split["alert"] is False
    assert split["split"] is True
    assert split["mean_g_norm"] == 0.85


def test_mean_rate_alerts_when_the_average_is_falling():
    out = combine_angles([
        _angle("a", rate=-0.02, declining=False),
        _angle("b", rate=-0.01, declining=False),
    ])
    assert out["alert"] is True
    assert out["alert_basis"] == "mean"


def test_nearest_frame_holds_slower_camera_and_gaps_out():
    frames = [
        {"sampled_at": 0, "image_path": "a"},
        {"sampled_at": 3600, "image_path": "b"},
    ]
    held = nearest_frame(frames, 1000, max_gap_s=7200)
    assert held["image_path"] == "a"
    assert nearest_frame(frames, 20000, max_gap_s=3600) is None


def test_align_playback_locks_different_frame_rates():
    fast = [{"sampled_at": t, "image_path": "f%d" % t} for t in (0, 900, 1800)]
    slow = [{"sampled_at": 0, "image_path": "s0"}, {"sampled_at": 1800, "image_path": "s1"}]
    aligned = align_playback([
        {"monitor_id": "fast", "name": "Front", "camera_id": 1, "frames": fast},
        {"monitor_id": "slow", "name": "Side", "camera_id": 2, "frames": slow},
    ], max_gap_s=2000)
    assert aligned["times"] == [0, 900, 1800]
    slow_col = aligned["angles"][1]["frames"]
    assert slow_col[1]["image_path"] == "s0"
    assert slow_col[1]["held_s"] == 900
    assert slow_col[2]["image_path"] == "s1"


def test_agent_brief_keeps_each_angle():
    brief = build_agent_brief([
        {
            "id": "a",
            "name": "rosemary",
            "camera_id": 10,
            "species_hint": "Rosmarinus",
            "subject_id": "subject-9",
            "subject_name": "Rosemary",
            "last_color_sample": {"G_norm": 0.8, "coverage": 0.1, "sampled_at": 10},
            "trends": {"greenness_rate_per_day": -0.01, "declining_greenness": True, "trend_metric": "G_norm"},
            "last_analysis": {"health_score": 70, "result": {"summary": "a bit dull"}},
        },
        {
            "id": "b",
            "name": "rosemary 2",
            "camera_id": 11,
            "subject_id": "subject-9",
            "subject_name": "Rosemary",
            "last_color_sample": {"G_norm": 0.6, "coverage": 0.2, "sampled_at": 40},
            "trends": {"greenness_rate_per_day": 0.0, "declining_greenness": False},
            "last_analysis": None,
        },
    ])
    assert brief["version"] == 1
    assert brief["plant"]["name"] == "Rosemary"
    assert brief["plant"]["species_hint"] == "Rosmarinus"
    assert len(brief["angles"]) == 2
    assert brief["angles"][0]["health_score"] == 70
    assert brief["combined"]["split"] is True
    assert "one plant" in brief["instruction"].lower() or "ONE" in brief["instruction"] or "one plant" in brief["instruction"]


def test_rename_solo_plant_and_grouped_angles():
    solo = rename_updates({"id": "a", "name": "kk"}, [{"id": "a"}], plant_name="Kitchen")
    assert solo == [{"id": "a", "name": "Kitchen"}]

    members = [
        {"id": "a", "subject_id": "subject-1", "name": "rosemary"},
        {"id": "b", "subject_id": "subject-1", "name": "rosemary 2"},
    ]
    grouped = rename_updates(members[0], members, plant_name="Rosemary", angle_name="Front")
    by_id = {row["id"]: row for row in grouped}
    assert by_id["a"]["subject_name"] == "Rosemary"
    assert by_id["a"]["name"] == "Front"
    assert by_id["b"]["subject_name"] == "Rosemary"
    assert "name" not in by_id["b"]
