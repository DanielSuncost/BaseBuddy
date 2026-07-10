"""HTTP client for BaseBuddy Cloud inference API."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from basebuddy.core.inference.exceptions import CloudNotConfigured, CloudQuotaExceeded, InferenceError
from basebuddy.core.inference.types import BoundingBox, ClassificationResult, Detection, DetectionResult


@dataclass
class CloudUsage:
    frames_consumed: int = 0
    quota_remaining: Optional[int] = None


class CloudClient:
    def __init__(self, endpoint: str, api_key: str, timeout_s: float = 5.0):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key.strip()
        self.timeout_s = timeout_s

    def _configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        if not self._configured():
            raise CloudNotConfigured("Set INFERENCE_CLOUD_ENDPOINT and INFERENCE_CLOUD_API_KEY")

        url = f"{self.endpoint}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            payload = {}
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            code = payload.get("code", "")
            if exc.code == 402 or code == "quota_exceeded":
                raise CloudQuotaExceeded(payload.get("error", "quota exceeded")) from exc
            raise InferenceError(payload.get("error", f"HTTP {exc.code}")) from exc
        except urllib.error.URLError as exc:
            raise InferenceError(f"Cloud unreachable: {exc}") from exc

    @staticmethod
    def _encode_jpeg(jpeg_bytes: bytes) -> str:
        return base64.b64encode(jpeg_bytes).decode("ascii")

    def detect(
        self,
        jpeg_bytes: bytes,
        *,
        camera_id: int,
        model_id: str = "yolov8s",
        conf_threshold: float = 0.35,
        instance_id: Optional[str] = None,
    ) -> DetectionResult:
        body: Dict[str, Any] = {
            "image": self._encode_jpeg(jpeg_bytes),
            "camera_id": camera_id,
            "model_id": model_id,
            "conf_threshold": conf_threshold,
        }
        if instance_id:
            body["instance_id"] = instance_id

        data = self._request("POST", "/inference/detect", body)
        detections: List[Detection] = []
        for item in data.get("detections", []):
            bbox = item.get("bbox", {})
            detections.append(
                Detection(
                    bbox=BoundingBox(
                        float(bbox.get("x1", 0)),
                        float(bbox.get("y1", 0)),
                        float(bbox.get("x2", 0)),
                        float(bbox.get("y2", 0)),
                    ),
                    class_id=int(item.get("class_id", -1)),
                    class_name=str(item.get("class_name", "unknown")),
                    confidence=float(item.get("confidence", 0)),
                )
            )
        return DetectionResult(
            detections=detections,
            inference_ms=float(data.get("inference_ms", 0)),
            model_id=str(data.get("model_id", model_id)),
            provider="cloud",
        )

    def segment(
        self,
        jpeg_bytes: bytes,
        *,
        points: List[Tuple[float, float]],
        labels: List[int],
        model_id: str = "sam_vit_b",
    ) -> dict:
        return self._request(
            "POST",
            "/inference/segment",
            {
                "image": self._encode_jpeg(jpeg_bytes),
                "points": [list(p) for p in points],
                "labels": labels,
                "model_id": model_id,
            },
        )

    def classify(self, jpeg_bytes: bytes, *, model_id: str) -> ClassificationResult:
        data = self._request(
            "POST",
            "/inference/classify",
            {"image": self._encode_jpeg(jpeg_bytes), "model_id": model_id},
        )
        return ClassificationResult(
            label=str(data.get("label", "unknown")),
            confidence=float(data.get("confidence", 0)),
            inference_ms=float(data.get("inference_ms", 0)),
            model_id=model_id,
            provider="cloud",
        )

    def get_usage(self) -> CloudUsage:
        data = self._request("GET", "/account/usage")
        usage = data.get("usage", data)
        return CloudUsage(
            frames_consumed=int(usage.get("frames_consumed", 0)),
            quota_remaining=usage.get("quota_remaining"),
        )

    def create_training_job(self, dataset_id: str, base_model: str, job_type: str = "yolo") -> dict:
        return self._request(
            "POST",
            "/training/jobs",
            {"dataset_id": dataset_id, "base_model": base_model, "job_type": job_type},
        )

    def register_training_dataset(
        self,
        *,
        dataset_id: str,
        remote_uri: str,
        dataset_type: str = "yolo",
        manifest: Optional[dict] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/training/datasets",
            {
                "dataset_id": dataset_id,
                "remote_uri": remote_uri,
                "dataset_type": dataset_type,
                "manifest": manifest or {},
            },
        )

    def get_training_job(self, job_id: str) -> dict:
        return self._request("GET", f"/training/jobs/{job_id}")

    def list_training_jobs(self) -> dict:
        return self._request("GET", "/training/jobs")
