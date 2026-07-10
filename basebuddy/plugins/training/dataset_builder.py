"""Build YOLO + person re-ID datasets from gallery labels."""
from __future__ import annotations

import csv
import json
import os
import random
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from basebuddy.core.inference.types import COCO_CLASSES


def _resolve_media_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    from basebuddy.modules.config import MEDIA_BASE_DIR, MEDIA_URL_PREFIX

    if path.startswith(MEDIA_URL_PREFIX):
        return os.path.join(MEDIA_BASE_DIR, path[len(MEDIA_URL_PREFIX):].lstrip("/"))
    if path.startswith("/media/"):
        return os.path.join(MEDIA_BASE_DIR, path[7:].lstrip("/"))
    if os.path.isfile(path):
        return path
    return None


def get_label_stats(hours: int = 8760) -> dict:
    import basebuddy.modules.state as shared_state

    db = shared_state.analytics_db
    rows = db.get_events_for_export(hours=hours, limit=10000)
    zones = db.list_false_positive_zones(limit=5000)
    fps = [r for r in rows if r.get("training_label") == "false_positive"]
    labeled = [r for r in rows if r.get("training_label") and r.get("training_label") != "false_positive"]
    persons = [r for r in rows if r.get("labeled_person_id")]
    person_emb = _count_person_embeddings()
    return {
        "hours": hours,
        "total_exportable": len(rows),
        "false_positives": len(fps),
        "labeled_detections": len(labeled),
        "person_labels": len(persons),
        "false_positive_zones": len(zones),
        "person_embeddings": person_emb,
        "named_people": _count_named_people(),
    }


def _count_person_embeddings() -> int:
    import basebuddy.modules.state as shared_state

    with shared_state.analytics_db._connect() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM person_embeddings")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _count_named_people() -> int:
    import basebuddy.modules.state as shared_state

    with shared_state.analytics_db._connect() as conn:
        cur = conn.execute("SELECT COUNT(*) FROM people WHERE is_unknown = 0")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def build_yolo_dataset(
    *,
    name: str,
    hours: int = 8760,
    val_ratio: float = 0.2,
    labeled_only: bool = False,
    include_negatives: bool = True,
    seed: int = 42,
) -> dict:
    import basebuddy.modules.state as shared_state

    dataset_id = f"ds-{uuid.uuid4().hex[:10]}"
    from basebuddy.core.paths import get_repo_root

    root = os.path.join(get_repo_root(), "training_datasets", dataset_id)
    for sub in ("images/train", "images/val", "labels/train", "labels/val", "negatives"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    db = shared_state.analytics_db
    rows = db.get_events_for_export(hours=hours, limit=10000)
    zones = db.list_false_positive_zones(limit=5000)

    if labeled_only:
        rows = [
            r for r in rows
            if r.get("training_label") or r.get("labeled_person_id") or r.get("user_label")
        ]

    positives = [r for r in rows if r.get("training_label") != "false_positive"]
    negatives = [r for r in rows if r.get("training_label") == "false_positive"] if include_negatives else []

    rng = random.Random(seed)
    rng.shuffle(positives)
    split = max(1, int(len(positives) * (1 - val_ratio))) if positives else 0
    train_rows = positives[:split] if positives else []
    val_rows = positives[split:] if len(positives) > split else []

    coco_index = {n: i for i, n in enumerate(COCO_CLASSES)}
    custom_names = set()
    for row in positives:
        ident = (row.get("identity_label") or row.get("corrected_class") or "").strip()
        if ident and ident not in COCO_CLASSES:
            custom_names.add(ident)
    all_classes = list(COCO_CLASSES) + sorted(custom_names)
    class_to_id = {n: i for i, n in enumerate(all_classes)}
    stats = {"train": 0, "val": 0, "negatives": 0, "skipped": 0, "custom_classes": sorted(custom_names)}

    for split_name, split_rows in (("train", train_rows), ("val", val_rows)):
        for row in split_rows:
            ok = _copy_yolo_sample(row, root, split_name, class_to_id)
            if ok:
                stats[split_name] += 1
            else:
                stats["skipped"] += 1

    for row in negatives:
        src = _resolve_media_path(row.get("full_image_path") or row.get("thumbnail_path"))
        if not src:
            stats["skipped"] += 1
            continue
        eid = row["id"]
        ext = os.path.splitext(src)[1] or ".jpg"
        dest = os.path.join(root, "negatives", f"{eid}{ext}")
        try:
            shutil.copy2(src, dest)
            stats["negatives"] += 1
        except OSError:
            stats["skipped"] += 1

    manifest = {
        "schema": "basebuddy.training.dataset/v1",
        "dataset_id": dataset_id,
        "name": name,
        "type": "yolo",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hours": hours,
        "val_ratio": val_ratio,
        "labeled_only": labeled_only,
        "stats": stats,
        "false_positive_zones": zones,
        "classes": all_classes,
        "layout": {
            "images/train": "YOLO train images",
            "images/val": "YOLO val images",
            "labels/train": "YOLO train labels",
            "labels/val": "YOLO val labels",
            "negatives": "Hard-negative images (no label file)",
            "person_labels": "Optional person re-ID pack",
        },
    }
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    with open(os.path.join(root, "data.yaml"), "w", encoding="utf-8") as fh:
        fh.write(f"path: {root}\n")
        fh.write("train: images/train\n")
        fh.write("val: images/val\n")
        fh.write(f"nc: {len(all_classes)}\n")
        fh.write(f"names: {all_classes}\n")

    from basebuddy.plugins.training.db import save_dataset

    rec = save_dataset({
        "id": dataset_id,
        "name": name,
        "dataset_type": "yolo",
        "status": "ready",
        "local_path": root,
        "stats": stats,
        "manifest": manifest,
    })
    return rec


def build_person_reid_dataset(*, name: str, hours: int = 8760) -> dict:
    """Export person_embeddings + identity.csv for re-ID training."""
    import basebuddy.modules.state as shared_state

    dataset_id = f"reid-{uuid.uuid4().hex[:10]}"
    from basebuddy.core.paths import get_repo_root

    root = os.path.join(get_repo_root(), "training_datasets", dataset_id)
    crops_dir = os.path.join(root, "person_labels", "crops")
    os.makedirs(crops_dir, exist_ok=True)

    copied = 0
    skipped = 0
    identities: List[dict] = []

    with shared_state.analytics_db._connect() as conn:
        cur = conn.execute("""
            SELECT pe.id, pe.person_id, pe.image_path, pe.camera_id, pe.timestamp, pe.confidence,
                   p.name, p.is_unknown
            FROM person_embeddings pe
            LEFT JOIN people p ON p.id = pe.person_id
            ORDER BY pe.timestamp DESC
            LIMIT 5000
        """)
        rows = cur.fetchall()

    for row in rows:
        emb_id, person_id, img_path, cam_id, ts, conf, pname, is_unknown = row
        src = _resolve_media_path(img_path)
        if not src or not os.path.isfile(src):
            skipped += 1
            continue
        ext = os.path.splitext(src)[1] or ".jpg"
        dest_name = f"{emb_id}{ext}"
        dest = os.path.join(crops_dir, dest_name)
        try:
            shutil.copy2(src, dest)
            copied += 1
            identities.append({
                "embedding_id": emb_id,
                "person_id": person_id,
                "identity": pname if pname and not is_unknown else f"unknown_{person_id}",
                "is_unknown": bool(is_unknown),
                "image": f"person_labels/crops/{dest_name}",
                "camera_id": cam_id,
                "timestamp": ts,
                "confidence": conf,
            })
        except OSError:
            skipped += 1

    csv_path = os.path.join(root, "person_labels", "identity.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        if identities:
            writer = csv.DictWriter(fh, fieldnames=list(identities[0].keys()))
            writer.writeheader()
            writer.writerows(identities)

    manifest = {
        "schema": "basebuddy.training.dataset/v1",
        "dataset_id": dataset_id,
        "name": name,
        "type": "person_reid",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stats": {"identities": len(identities), "skipped": skipped},
    }
    with open(os.path.join(root, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    from basebuddy.plugins.training.db import save_dataset

    return save_dataset({
        "id": dataset_id,
        "name": name,
        "dataset_type": "person_reid",
        "status": "ready",
        "local_path": root,
        "stats": manifest["stats"],
        "manifest": manifest,
    })


def _copy_yolo_sample(row: dict, root: str, split: str, class_to_id: dict) -> bool:
    src = _resolve_media_path(row.get("full_image_path") or row.get("thumbnail_path"))
    if not src:
        return False
    eid = row["id"]
    ext = os.path.splitext(src)[1] or ".jpg"
    dest = os.path.join(root, "images", split, f"{eid}{ext}")
    try:
        shutil.copy2(src, dest)
    except OSError:
        return False

    cls = (
        (row.get("identity_label") or "").strip()
        or (row.get("corrected_class") or "").strip()
        or row.get("class_name")
        or "object"
    )
    cid = class_to_id.get(cls, 0)
    x1, y1, x2, y2 = row["bbox_x1"], row["bbox_y1"], row["bbox_x2"], row["bbox_y2"]
    try:
        from PIL import Image

        with Image.open(dest) as im:
            w, h = im.size
        if w <= 0 or h <= 0:
            return False
        cx = ((x1 + x2) / 2) / w
        cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        lbl_path = os.path.join(root, "labels", split, f"{eid}.txt")
        with open(lbl_path, "w", encoding="utf-8") as lf:
            lf.write(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
        return True
    except Exception:
        return False
