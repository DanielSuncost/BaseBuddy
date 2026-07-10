"""
Automatic AI model management for BaseBuddy.

Downloads YOLO (and optional SAM) weights, assesses local GPU/RAM/disk,
and recommends a model set for the host.
"""
from __future__ import annotations

import os
import shutil
import sys
import urllib.request
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

MODEL_URLS = {
    "yolov8n.pt": "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt",
    "yolov8s.pt": "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s.pt",
    "yolov8m.pt": "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8m.pt",
    "yolov8n-pose.pt": "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n-pose.pt",
    "sam_vit_b_01ec64.pth": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth",
}

# Approximate download size and minimum resources to run inference comfortably
MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    "yolov8n.pt": {
        "label": "YOLOv8 Nano",
        "kind": "detection",
        "size_mb": 6,
        "vram_min_mb": 512,
        "ram_min_mb": 2048,
        "cpu_ok": True,
    },
    "yolov8s.pt": {
        "label": "YOLOv8 Small (day model)",
        "kind": "detection",
        "size_mb": 22,
        "vram_min_mb": 1200,
        "ram_min_mb": 4096,
        "cpu_ok": True,
    },
    "yolov8m.pt": {
        "label": "YOLOv8 Medium (night model)",
        "kind": "detection",
        "size_mb": 50,
        "vram_min_mb": 2500,
        "ram_min_mb": 8192,
        "cpu_ok": False,
    },
    "yolov8n-pose.pt": {
        "label": "YOLOv8 Nano Pose",
        "kind": "pose",
        "size_mb": 6,
        "vram_min_mb": 768,
        "ram_min_mb": 4096,
        "cpu_ok": True,
    },
    "sam_vit_b_01ec64.pth": {
        "label": "SAM ViT-B (plant segmentation)",
        "kind": "segmentation",
        "size_mb": 375,
        "vram_min_mb": 2048,
        "ram_min_mb": 8192,
        "cpu_ok": True,
    },
}

PROFILE_LABELS = {
    "cpu_minimal": "CPU only — YOLOv8 Nano recommended",
    "gpu_light": "Light GPU — nano + small models",
    "gpu_standard": "Standard GPU — adaptive day/night models",
    "gpu_full": "Capable GPU — all detection + optional SAM",
}


def project_root() -> str:
    from basebuddy.core.paths import get_repo_root
    return get_repo_root()


def models_dir() -> str:
    path = os.path.join(project_root(), "models")
    os.makedirs(path, exist_ok=True)
    return path


def get_model_path(model_name: str) -> Optional[str]:
    """Return full path if the weight file exists."""
    root = project_root()
    for candidate in (
        os.path.join(root, model_name),
        os.path.join(root, "models", model_name),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 1)
    except OSError:
        return 0.0


def get_required_model_names() -> List[str]:
    """Models referenced by current config (deduplicated, ordered)."""
    from basebuddy.modules.config import ADAPTIVE_MODE, AI_MODEL, DAY_MODEL, NIGHT_MODEL

    names: List[str] = []
    if ADAPTIVE_MODE:
        names.extend([DAY_MODEL, NIGHT_MODEL])
    else:
        names.append(AI_MODEL)
    # Always ensure a fallback nano is listed for fresh installs
    if "yolov8n.pt" not in names:
        names.insert(0, "yolov8n.pt")
    seen = set()
    out: List[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def assess_system() -> Dict[str, Any]:
    """Inspect GPU, RAM, and disk for local inference readiness."""
    cuda_available = False
    gpu_name: Optional[str] = None
    gpu_vram_total_mb = 0.0
    gpu_vram_free_mb = 0.0

    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_name = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            gpu_vram_total_mb = round(props.total_memory / (1024 * 1024), 0)
            try:
                free_b, total_b = torch.cuda.mem_get_info(0)
                gpu_vram_free_mb = round(free_b / (1024 * 1024), 0)
                gpu_vram_total_mb = round(total_b / (1024 * 1024), 0)
            except Exception:
                gpu_vram_free_mb = gpu_vram_total_mb * 0.7
    except Exception:
        pass

    try:
        from basebuddy.modules.resource_manager import get_resource_manager
        stats = get_resource_manager().monitor.get_gpu_stats()
        if stats:
            gpu_name = gpu_name or stats.name
            gpu_vram_total_mb = gpu_vram_total_mb or stats.memory_total_mb
            gpu_vram_free_mb = stats.memory_free_mb
    except Exception:
        pass

    ram_total_gb = ram_available_gb = 0.0
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram_total_gb = round(vm.total / (1024 ** 3), 1)
        ram_available_gb = round(vm.available / (1024 ** 3), 1)
    except Exception:
        pass

    disk_free_gb = 0.0
    try:
        disk_free_gb = round(shutil.disk_usage(models_dir()).free / (1024 ** 3), 1)
    except Exception:
        pass

    vram_for_profile = gpu_vram_free_mb if gpu_vram_free_mb > 0 else gpu_vram_total_mb

    if not cuda_available or vram_for_profile < 800:
        profile = "cpu_minimal"
    elif vram_for_profile < 2500:
        profile = "gpu_light"
    elif vram_for_profile < 4500:
        profile = "gpu_standard"
    else:
        profile = "gpu_full"

    return {
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "gpu_vram_total_mb": gpu_vram_total_mb,
        "gpu_vram_free_mb": gpu_vram_free_mb,
        "ram_total_gb": ram_total_gb,
        "ram_available_gb": ram_available_gb,
        "disk_free_gb": disk_free_gb,
        "profile": profile,
        "profile_label": PROFILE_LABELS.get(profile, profile),
    }


def _sufficient_for_model(model_name: str, system: Dict[str, Any]) -> bool:
    meta = MODEL_CATALOG.get(model_name, {})
    vram_need = float(meta.get("vram_min_mb", 512))
    ram_need_gb = float(meta.get("ram_min_mb", 2048)) / 1024.0
    ram_ok = system.get("ram_available_gb", 0) >= ram_need_gb or system.get("ram_total_gb", 0) >= ram_need_gb

    if not system.get("cuda_available"):
        return bool(meta.get("cpu_ok", True)) and ram_ok

    vram = system.get("gpu_vram_free_mb") or system.get("gpu_vram_total_mb") or 0
    return vram >= vram_need and ram_ok


def _recommended_for_profile(model_name: str, profile: str) -> bool:
    if profile == "cpu_minimal":
        return model_name in ("yolov8n.pt",)
    if profile == "gpu_light":
        return model_name in ("yolov8n.pt", "yolov8s.pt")
    if profile == "gpu_standard":
        return model_name in ("yolov8n.pt", "yolov8s.pt", "yolov8m.pt")
    return model_name in MODEL_CATALOG


def get_models_status() -> Dict[str, Any]:
    """Full status payload for the config UI."""
    system = assess_system()
    required = get_required_model_names()
    profile = system["profile"]

    models: List[Dict[str, Any]] = []
    for model_name, meta in MODEL_CATALOG.items():
        path = get_model_path(model_name)
        installed = path is not None
        models.append({
            "id": model_name,
            "label": meta.get("label", model_name),
            "kind": meta.get("kind", "detection"),
            "installed": installed,
            "path": path,
            "on_disk_mb": _file_size_mb(path) if path else 0,
            "download_size_mb": meta.get("size_mb"),
            "download_url": MODEL_URLS.get(model_name),
            "sufficient": _sufficient_for_model(model_name, system),
            "recommended": _recommended_for_profile(model_name, profile),
            "required": model_name in required,
            "cpu_ok": meta.get("cpu_ok", True),
        })

    missing_required = [m["id"] for m in models if m["required"] and not m["installed"]]
    recommended_missing = [
        m["id"] for m in models if m["recommended"] and not m["installed"] and m["id"] in MODEL_URLS
    ]

    total_download_mb = sum(
        MODEL_CATALOG[mid]["size_mb"] for mid in recommended_missing if mid in MODEL_CATALOG
    )

    return {
        "system": system,
        "required_models": required,
        "models": models,
        "missing_required": missing_required,
        "recommended_missing": recommended_missing,
        "all_required_installed": len(missing_required) == 0,
        "recommended_download_mb": total_download_mb,
        "can_run_detection": any(m["installed"] and m["kind"] == "detection" for m in models),
    }


def download_model(model_name: str, target_dir: str = "models") -> Optional[str]:
    """Download a weight file if missing. Returns path on success."""
    existing = get_model_path(model_name)
    if existing:
        return existing

    if model_name not in MODEL_URLS:
        logger.warning(f"Unknown model: {model_name}")
        return None

    base_dir = project_root()
    target_path = os.path.join(base_dir, target_dir)
    os.makedirs(target_path, exist_ok=True)
    model_file = os.path.join(target_path, model_name)

    url = MODEL_URLS[model_name]
    logger.info(f"Downloading {model_name}...")
    logger.info(f"Source: {url}")
    logger.info(f"Target: {model_file}")

    try:
        def report_progress(block_num, block_size, total_size):
            if total_size <= 0:
                return
            downloaded = block_num * block_size
            percent = min(downloaded * 100 / total_size, 100)
            sys.stdout.write(f"\r   Progress: {percent:.1f}%")
            sys.stdout.flush()

        urllib.request.urlretrieve(url, model_file, reporthook=report_progress)
        logger.info(f"\n Downloaded {model_name} successfully!")
        return model_file
    except Exception as exc:
        logger.error(f"\n Failed to download {model_name}: {exc}")
        if os.path.isfile(model_file):
            try:
                os.remove(model_file)
            except OSError:
                pass
        return None


def download_models(model_names: List[str]) -> Dict[str, Any]:
    """Download multiple models; returns per-model results."""
    results = []
    for name in model_names:
        if name not in MODEL_URLS:
            results.append({"id": name, "ok": False, "error": "unknown model"})
            continue
        path = download_model(name)
        results.append({
            "id": name,
            "ok": path is not None,
            "path": path,
            "error": None if path else "download failed",
        })
    return {
        "downloaded": [r["id"] for r in results if r["ok"]],
        "failed": [r for r in results if not r["ok"]],
        "results": results,
    }


def resolve_download_set(scope: str = "recommended", explicit: Optional[List[str]] = None) -> List[str]:
    """Pick which models to download for a one-click action."""
    status = get_models_status()
    if explicit:
        return [m for m in explicit if m in MODEL_URLS]

    if scope == "required":
        return list(status["missing_required"])
    if scope == "missing":
        return [
            m["id"] for m in status["models"]
            if not m["installed"] and m["id"] in MODEL_URLS
        ]
    if scope == "all":
        return list(MODEL_URLS.keys())

    # default: recommended profile, not yet installed
    return list(status["recommended_missing"])


def ensure_models_available(required_models=None) -> bool:
    """Ensure required models exist (startup helper)."""
    if required_models is None:
        required_models = ["yolov8n.pt", "yolov8s.pt"]

    logger.info("Checking AI models...")
    missing = [m for m in required_models if get_model_path(m) is None]
    if not missing:
        logger.info("All required models present")
        return True

    logger.warning(f"Missing models: {', '.join(missing)}")
    logger.info("Starting automatic download...")
    result = download_models(missing)
    return len(result["failed"]) == 0


def get_available_models() -> List[str]:
    """List installed .pt / .pth weight filenames."""
    available: List[str] = []
    root = project_root()
    for model_name in MODEL_URLS:
        if get_model_path(model_name):
            available.append(model_name)
    models_path = os.path.join(root, "models")
    if os.path.isdir(models_path):
        for filename in os.listdir(models_path):
            if filename.endswith((".pt", ".pth")) and filename not in available:
                available.append(filename)
    return available


def export_model_backend(model_name: str, backend: str = "tensorrt") -> Dict[str, Any]:
    """Export .pt to TensorRT (.engine) or OpenVINO (requires ultralytics + backend)."""
    path = get_model_path(model_name) or model_name
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": f"Model not found: {model_name}"}
    try:
        from ultralytics import YOLO
        m = YOLO(path)
        fmt = "engine" if backend == "tensorrt" else "openvino"
        out = m.export(format=fmt)
        return {"ok": True, "backend": backend, "output": str(out)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    logger.info("BaseBuddy Model Manager")
    logger.info("="* 50)
    import json
    logger.info(json.dumps(get_models_status(), indent=2))
    ensure_models_available()
