"""Training REST API."""
from __future__ import annotations

import os
import shutil

from flask import Blueprint, jsonify, request

from basebuddy.plugins.training.dataset_builder import (
    build_person_reid_dataset,
    build_yolo_dataset,
    get_label_stats,
)
from basebuddy.plugins.training.dataset_storage import upload_dataset_to_remote
from basebuddy.plugins.training.db import delete_dataset, get_dataset, get_job, list_datasets, list_jobs
from basebuddy.plugins.training.local_trainer import (
    deploy_model_weights,
    gpu_info,
    start_local_yolo_job,
    submit_cloud_training_job,
)

training_api = Blueprint("training_api", __name__)


@training_api.route("/stats", methods=["GET"])
def api_stats():
    hours = int(request.args.get("hours", 8760))
    return jsonify({"ok": True, "stats": get_label_stats(hours=hours), "gpu": gpu_info()})


@training_api.route("/datasets", methods=["GET"])
def api_list_datasets():
    return jsonify({"ok": True, "datasets": list_datasets()})


@training_api.route("/datasets", methods=["POST"])
def api_build_dataset():
    body = request.get_json(silent=True) or {}
    dtype = (body.get("type") or "yolo").lower()
    name = (body.get("name") or "Training dataset").strip()
    hours = int(body.get("hours") or 8760)
    try:
        if dtype == "person_reid":
            ds = build_person_reid_dataset(name=name, hours=hours)
        else:
            ds = build_yolo_dataset(
                name=name,
                hours=hours,
                val_ratio=float(body.get("val_ratio") or 0.2),
                labeled_only=bool(body.get("labeled_only")),
                include_negatives=body.get("include_negatives", True) is not False,
            )
        return jsonify({"ok": True, "dataset": ds})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@training_api.route("/datasets/<dataset_id>", methods=["GET"])
def api_get_dataset(dataset_id):
    ds = get_dataset(dataset_id)
    if not ds:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "dataset": ds})


@training_api.route("/datasets/<dataset_id>", methods=["DELETE"])
def api_delete_dataset(dataset_id):
    ds = get_dataset(dataset_id)
    if not ds:
        return jsonify({"ok": False, "error": "not found"}), 404
    if ds.get("local_path") and os.path.isdir(ds["local_path"]):
        shutil.rmtree(ds["local_path"], ignore_errors=True)
    delete_dataset(dataset_id)
    return jsonify({"ok": True})


@training_api.route("/datasets/<dataset_id>/upload", methods=["POST"])
def api_upload_dataset(dataset_id):
    try:
        result = upload_dataset_to_remote(dataset_id)
        code = 200 if result.get("ok") else 400
        return jsonify(result), code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@training_api.route("/jobs", methods=["GET"])
def api_list_jobs():
    return jsonify({"ok": True, "jobs": list_jobs()})


@training_api.route("/jobs/<job_id>", methods=["GET"])
def api_get_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "not found"}), 404
    if job.get("cloud_job_id") and job.get("status") in ("submitted", "running"):
        try:
            from basebuddy.modules.config import INFERENCE_CLOUD_API_KEY, INFERENCE_CLOUD_ENDPOINT
            from basebuddy.core.inference.cloud.client import CloudClient
            from basebuddy.plugins.training.db import update_job

            if INFERENCE_CLOUD_API_KEY and INFERENCE_CLOUD_ENDPOINT:
                client = CloudClient(INFERENCE_CLOUD_ENDPOINT, INFERENCE_CLOUD_API_KEY, timeout_s=15)
                remote = client.get_training_job(job["cloud_job_id"])
                if remote.get("status"):
                    update_job(job_id, status=remote["status"], metrics=remote.get("metrics"), log_text=str(remote))
                    job = get_job(job_id)
        except Exception:
            pass
    return jsonify({"ok": True, "job": job})


@training_api.route("/jobs/local", methods=["POST"])
def api_start_local_job():
    body = request.get_json(silent=True) or {}
    dataset_id = body.get("dataset_id") or ""
    result = start_local_yolo_job(
        dataset_id,
        base_model=body.get("base_model") or "yolov8n.pt",
        epochs=int(body.get("epochs") or 50),
        imgsz=int(body.get("imgsz") or 640),
        batch=int(body.get("batch") or 8),
    )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@training_api.route("/jobs/cloud", methods=["POST"])
def api_start_cloud_job():
    body = request.get_json(silent=True) or {}
    result = submit_cloud_training_job(
        body.get("dataset_id") or "",
        base_model=body.get("base_model") or "yolov8n",
        job_type=body.get("job_type") or "yolo",
    )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@training_api.route("/jobs/<job_id>/deploy", methods=["POST"])
def api_deploy_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "not found"}), 404
    path = job.get("output_path")
    result = deploy_model_weights(path)
    code = 200 if result.get("ok") else 400
    return jsonify(result), code
