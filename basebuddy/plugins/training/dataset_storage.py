"""Upload training datasets to local archive paths and S3/R2 containers."""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

DATASET_REMOTE_CATEGORY = "training-datasets"


def get_s3_backend():
    from basebuddy.core.storage_policy_runtime import build_user_s3_backend
    import basebuddy.modules.config as cfg

    return build_user_s3_backend(cfg)


def upload_dataset_to_remote(dataset_id: str) -> Dict:
    from basebuddy.plugins.training.db import get_dataset, update_dataset

    ds = get_dataset(dataset_id)
    if not ds:
        return {"ok": False, "error": "Dataset not found"}
    local_path = ds.get("local_path")
    if not local_path or not os.path.isdir(local_path):
        return {"ok": False, "error": "Local dataset path missing"}

    backend = get_s3_backend()
    if not backend.is_configured:
        return {"ok": False, "error": "Remote storage (R2/S3) not configured — set REMOTE_* in Storage settings"}

    update_dataset(dataset_id, status="uploading")
    uploaded = 0
    failed = 0
    total_bytes = 0

    for dirpath, _dirnames, filenames in os.walk(local_path):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, local_path).replace("\\", "/")
            remote_rel = f"{dataset_id}/{rel}"
            ok, _msg = backend.upload_file(full, DATASET_REMOTE_CATEGORY, remote_rel)
            if ok:
                uploaded += 1
                try:
                    total_bytes += os.path.getsize(full)
                except OSError:
                    pass
            else:
                failed += 1

    remote_uri = f"s3://{backend.bucket}/{backend.object_key(DATASET_REMOTE_CATEGORY, dataset_id + '/')}".rstrip("/")
    if backend.prefix:
        remote_uri = f"s3://{backend.bucket}/{backend.prefix}/{DATASET_REMOTE_CATEGORY}/{dataset_id}"

    status = "remote_ready" if failed == 0 else ("partial" if uploaded else "upload_failed")
    update_dataset(
        dataset_id,
        status=status,
        remote_uri=remote_uri,
        stats={**(ds.get("stats") or {}), "remote_uploaded": uploaded, "remote_failed": failed, "remote_bytes": total_bytes},
    )
    return {
        "ok": uploaded > 0,
        "uploaded": uploaded,
        "failed": failed,
        "remote_uri": remote_uri,
        "status": status,
    }


def dataset_remote_prefix(dataset_id: str) -> str:
    backend = get_s3_backend()
    return backend.object_key(DATASET_REMOTE_CATEGORY, dataset_id)
