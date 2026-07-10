#!/usr/bin/env python3
"""
Discover public municipal/DOT camera streams from 511NY and add them to BaseBuddy.

511NY (NYSDOT) exposes camera metadata publicly at /api/getcameras; many cameras
publish an HLS stream (playlist.m3u8) that BaseBuddy's frame grabber can consume.

Usage:
    # List cameras in a bounding box (required unless you set DEFAULT_BBOX below)
    python scripts/fetch_municipal_cameras.py --bbox 40.5 -74.5 41.0 -73.5

    # Filter by name and limit results
    python scripts/fetch_municipal_cameras.py --bbox 40.5 -74.5 41.0 -73.5 --search "I-95" --limit 10

    # Write the first N live cameras into free CAM slots in config.txt
    python scripts/fetch_municipal_cameras.py --bbox 40.5 -74.5 41.0 -73.5 --add 4

Note: these are public feeds provided by NYSDOT via 511NY. Review the 511NY
developer terms (https://511ny.org/developers/help) for your use case.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAMERAS_URL = "https://511ny.org/api/getcameras?format=json"
# No default bbox — pass --bbox so your home area is not baked into the repo.
DEFAULT_BBOX = None
MAX_CAM_SLOTS = 20


def fetch_cameras():
    req = urllib.request.Request(CAMERAS_URL, headers={"User-Agent": "BaseBuddy/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def filter_cameras(cams, bbox, search=None):
    min_lat, min_lon, max_lat, max_lon = bbox
    out = []
    for c in cams:
        if c.get("Disabled") or c.get("Blocked") or not c.get("VideoUrl"):
            continue
        lat, lon = c.get("Latitude"), c.get("Longitude")
        if lat is None or lon is None:
            continue
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        if search and search.lower() not in (c.get("Name") or "").lower():
            continue
        out.append(c)
    return out


def probe_stream(url) -> bool:
    """Return True if the stream opens and yields a frame."""
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "loglevel;quiet")
    import cv2

    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    ok = False
    if cap.isOpened():
        ok, _ = cap.read()
    cap.release()
    return bool(ok)


def free_cam_slots():
    from basebuddy.modules.config import CAM_URLS

    return [i + 1 for i, url in enumerate(CAM_URLS) if not url]


def add_to_config(cameras):
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    slots = free_cam_slots()
    if len(cameras) > len(slots):
        print(f"Only {len(slots)} free CAM slots; truncating to fit.")
        cameras = cameras[: len(slots)]

    updates = {}
    for slot, cam in zip(slots, cameras):
        updates[f"CAM{slot}"] = cam["VideoUrl"]
        print(f"CAM{slot} (cam_id {slot - 1}): {cam['Name']}")
    upsert_config_exports(get_repo_root(), updates)
    print(f"\nWrote {len(updates)} cameras to config.txt. Restart BaseBuddy to start them.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("MIN_LAT", "MIN_LON", "MAX_LAT", "MAX_LON"),
                        default=DEFAULT_BBOX, help="Bounding box min-lat min-lon max-lat max-lon (required)")
    parser.add_argument("--search", help="Substring filter on camera name (e.g. 'I-95')")
    parser.add_argument("--limit", type=int, default=20, help="Max cameras to list/probe")
    parser.add_argument("--add", type=int, metavar="N", help="Add first N live cameras to config.txt")
    parser.add_argument("--no-probe", action="store_true", help="Skip stream liveness checks")
    args = parser.parse_args()

    if args.bbox is None:
        parser.error("--bbox is required (min-lat min-lon max-lat max-lon). Example: --bbox 40.5 -74.5 41.0 -73.5")

    cams = filter_cameras(fetch_cameras(), tuple(args.bbox), args.search)
    print(f"Found {len(cams)} active cameras with video streams in the area.\n")

    live = []
    for cam in cams[: args.limit]:
        if args.no_probe:
            alive = None
        else:
            alive = probe_stream(cam["VideoUrl"])
            if not alive:
                continue
        live.append(cam)
        status = "" if alive is None else "LIVE  "
        print(f"{status}{cam['Name'][:60]:60s} {cam['VideoUrl']}")
        if args.add and len(live) >= args.add:
            break

    if args.add:
        print()
        add_to_config(live[: args.add])


if __name__ == "__main__":
    main()
