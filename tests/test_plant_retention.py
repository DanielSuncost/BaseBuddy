"""Plant images follow their own retention window."""
import os
import time

from basebuddy.core.retention_config import retention_policy_from_env
from basebuddy.core.services.retention_service import RetentionService


def _age(path, days):
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def _service(tmp_path):
    stills = tmp_path / "stills" / "camera_1"
    growth = stills / "sam_growth" / "series_1"
    growth.mkdir(parents=True)
    media = tmp_path / "media"
    plant = media / "plant_health" / "cam1"
    plant.mkdir(parents=True)
    files = {
        "plant_still": growth / "frame.jpg",
        "camera_still": stills / "snap.jpg",
        "plant_media": plant / "capture.jpg",
        "detection": media / "crop.jpg",
    }
    for path in files.values():
        path.write_bytes(b"x")
    _age(files["plant_still"], 40)
    _age(files["plant_media"], 40)
    _age(files["camera_still"], 10)
    _age(files["detection"], 10)
    svc = RetentionService(
        source_dirs={"stills": str(tmp_path / "stills"), "detections": str(media)},
        retention_policy={
            "stills": {"local_days": 6, "remote_days": 0},
            "detections": {"local_days": 4, "remote_days": 0},
            "plants": {"local_days": 90, "remote_days": 0},
        },
        enabled=False,
    )
    return svc, files


def test_policy_keeps_plants_longer_than_other_media():
    raw = (
        '{"recordings":{"local_days":1,"remote_days":0},'
        '"detections":{"local_days":4,"remote_days":0},'
        '"stills":{"local_days":6,"remote_days":0},'
        '"plants":{"local_days":90,"remote_days":0},'
        '"timelapse":{"local_days":30,"remote_days":0}}'
    )
    policy = retention_policy_from_env(raw, 1)
    assert policy["plants"]["local_days"] == 90
    assert policy["detections"]["local_days"] == 4
    assert policy["stills"]["local_days"] == 6
    assert policy["recordings"]["local_days"] == 1


def test_plant_files_skip_the_shorter_windows(tmp_path):
    svc, files = _service(tmp_path)
    stills = [p for p, _, _ in svc._iter_category_files("stills", svc.source_dirs["stills"], 6 * 86400)]
    detections = [p for p, _, _ in svc._iter_category_files("detections", svc.source_dirs["detections"], 4 * 86400)]
    plants = []
    for root in (svc.source_dirs["stills"], svc.source_dirs["detections"]):
        plants.extend(p for p, _, _ in svc._iter_category_files("plants", root, 90 * 86400))

    assert files["camera_still"].as_posix() in stills
    assert files["plant_still"].as_posix() not in stills
    assert files["detection"].as_posix() in detections
    assert files["plant_media"].as_posix() not in detections
    assert plants == []

    _age(files["plant_still"], 100)
    aged = [p for p, _, _ in svc._iter_category_files("plants", svc.source_dirs["stills"], 90 * 86400)]
    assert files["plant_still"].as_posix() in aged


def test_disk_pressure_leaves_plant_files(tmp_path):
    svc, files = _service(tmp_path)
    found = {path for _, path, _, _ in svc._collect_oldest_media(("stills", "detections"))}
    assert files["camera_still"].as_posix() in found
    assert files["detection"].as_posix() in found
    assert files["plant_still"].as_posix() not in found
    assert files["plant_media"].as_posix() not in found
