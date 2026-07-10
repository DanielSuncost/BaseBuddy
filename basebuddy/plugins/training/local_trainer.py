"""Local Ultralytics YOLO fine-tuning in background thread."""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Optional


def gpu_info() -> dict:
    info = {"cuda_available": False, "device_name": None, "vram_gb": None}
    try:
        import torch

        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["device_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["vram_gb"] = round(getattr(props, "total_memory", 0) / (1024**3), 2)
    except ImportError:
        info["error"] = "PyTorch not installed"
    except Exception as exc:
        info["error"] = str(exc)
    return info


def start_local_yolo_job(
    dataset_id: str,
    *,
    base_model: str = "yolov8n.pt",
    epochs: int = 50,
    imgsz: int = 640,
    batch: int = 8,
) -> dict:
    from basebuddy.plugins.training.db import get_dataset, save_job

    ds = get_dataset(dataset_id)
    if not ds or ds.get("dataset_type") != "yolo":
        return {"ok": False, "error": "YOLO dataset not found"}
    data_yaml = os.path.join(ds["local_path"], "data.yaml")
    if not os.path.isfile(data_yaml):
        return {"ok": False, "error": "data.yaml missing in dataset"}

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    save_job({
        "id": job_id,
        "dataset_id": dataset_id,
        "job_type": "yolo_local",
        "status": "queued",
        "base_model": base_model,
    })

    t = threading.Thread(
        target=_run_yolo,
        args=(job_id, data_yaml, base_model, epochs, imgsz, batch),
        daemon=True,
        name=f"train-{job_id}",
    )
    t.start()
    return {"ok": True, "job_id": job_id}


def _run_yolo(job_id: str, data_yaml: str, base_model: str, epochs: int, imgsz: int, batch: int) -> None:
    from basebuddy.plugins.training.db import update_job
    from basebuddy.core.paths import get_repo_root

    update_job(job_id, status="running", started_at=time.time(), log_text="Starting Ultralytics training…")
    try:
        from ultralytics import YOLO
    except ImportError:
        update_job(job_id, status="failed", finished_at=time.time(), error="ultralytics not installed (pip install ultralytics)")
        return

    out_dir = os.path.join(get_repo_root(), "training_runs", job_id)
    os.makedirs(out_dir, exist_ok=True)
    logs: list[str] = []

    try:
        model = YOLO(base_model)
        results = model.train(
            data=data_yaml,
            epochs=int(epochs),
            imgsz=int(imgsz),
            batch=int(batch),
            project=out_dir,
            name="train",
            exist_ok=True,
            verbose=True,
        )
        best = os.path.join(out_dir, "train", "weights", "best.pt")
        if not os.path.isfile(best):
            best = os.path.join(out_dir, "train", "weights", "last.pt")
        metrics = {}
        if hasattr(results, "results_dict"):
            metrics = dict(results.results_dict)
        update_job(
            job_id,
            status="completed",
            finished_at=time.time(),
            output_path=best if os.path.isfile(best) else out_dir,
            metrics=metrics,
            log_text="\n".join(logs) or "Training completed",
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            finished_at=time.time(),
            error=str(exc),
            log_text="\n".join(logs),
        )


def deploy_model_weights(weights_path: str) -> dict:
    if not weights_path or not os.path.isfile(weights_path):
        return {"ok": False, "error": "Weights file not found"}
    from basebuddy.core.config_persist import upsert_config_exports
    from basebuddy.core.paths import get_repo_root

    upsert_config_exports(get_repo_root(), {"AI_MODEL": weights_path})
    os.environ["AI_MODEL"] = weights_path
    try:
        from basebuddy.modules.config import load_config_file
        load_config_file()
    except Exception:
        pass
    return {"ok": True, "ai_model": weights_path}


def submit_cloud_training_job(dataset_id: str, *, base_model: str = "yolov8n", job_type: str = "yolo") -> dict:
    from basebuddy.plugins.training.db import get_dataset, save_job, update_job
    from basebuddy.plugins.training.dataset_storage import upload_dataset_to_remote
    from basebuddy.modules.config import INFERENCE_CLOUD_API_KEY, INFERENCE_CLOUD_ENDPOINT

    ds = get_dataset(dataset_id)
    if not ds:
        return {"ok": False, "error": "Dataset not found"}

    if not ds.get("remote_uri"):
        up = upload_dataset_to_remote(dataset_id)
        if not up.get("ok"):
            return {"ok": False, "error": up.get("error") or "Remote upload failed"}
        ds = get_dataset(dataset_id)

    if not INFERENCE_CLOUD_API_KEY or not INFERENCE_CLOUD_ENDPOINT:
        return {
            "ok": False,
            "error": "Cloud training requires INFERENCE_CLOUD_ENDPOINT and INFERENCE_CLOUD_API_KEY",
        }

    job_id = f"job-{uuid.uuid4().hex[:10]}"
    save_job({
        "id": job_id,
        "dataset_id": dataset_id,
        "job_type": "yolo_cloud" if job_type == "yolo" else "person_reid_cloud",
        "status": "submitting",
        "base_model": base_model,
    })

    try:
        from basebuddy.core.inference.cloud.client import CloudClient

        client = CloudClient(INFERENCE_CLOUD_ENDPOINT, INFERENCE_CLOUD_API_KEY, timeout_s=30)
        reg = client.register_training_dataset(
            dataset_id=dataset_id,
            remote_uri=ds.get("remote_uri") or "",
            dataset_type=ds.get("dataset_type") or "yolo",
            manifest=ds.get("manifest") or {},
        )
        remote_dataset_id = reg.get("dataset_id") or dataset_id
        resp = client.create_training_job(remote_dataset_id, base_model, job_type=job_type)
        cloud_job_id = resp.get("job_id") or resp.get("id")
        update_job(
            job_id,
            status=resp.get("status") or "submitted",
            cloud_job_id=str(cloud_job_id) if cloud_job_id else None,
            log_text=str(resp),
        )
        return {"ok": True, "job_id": job_id, "cloud_job_id": cloud_job_id, "response": resp}
    except Exception as exc:
        update_job(job_id, status="failed", error=str(exc), finished_at=time.time())
        return {"ok": False, "error": str(exc), "job_id": job_id}
