"""S3-compatible object storage (AWS S3, Cloudflare R2, Backblaze B2, MinIO)."""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("basebuddy")


class S3ObjectStorage:
    """Upload, delete, and test connectivity for S3-compatible buckets."""

    def __init__(
        self,
        *,
        enabled: bool,
        provider: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
        endpoint_url: str = "",
        prefix: str = "",
    ):
        self.enabled = bool(enabled)
        self.provider = (provider or "s3").lower()
        self.bucket = (bucket or "").strip()
        self.access_key = (access_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.region = (region or "auto").strip()
        self.endpoint_url = (endpoint_url or "").strip().rstrip("/")
        self.prefix = (prefix or "").strip().strip("/")

    @property
    def is_configured(self) -> bool:
        return bool(
            self.enabled
            and self.bucket
            and self.access_key
            and self.secret_key
        )

    @property
    def is_active(self) -> bool:
        return self.is_configured

    def _client(self):
        import boto3
        from botocore.config import Config

        kwargs = {
            "service_name": "s3",
            "aws_access_key_id": self.access_key,
            "aws_secret_access_key": self.secret_key,
            "config": Config(signature_version="s3v4"),
        }
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self.region and self.region != "auto":
            kwargs["region_name"] = self.region
        return boto3.client(**kwargs)

    def object_key(self, category: str, rel_path: str) -> str:
        parts = [p for p in (self.prefix, category, rel_path.replace("\\", "/")) if p]
        return "/".join(parts)

    def upload_file(self, local_path: str, category: str, rel_path: str) -> Tuple[bool, str]:
        if not self.is_configured:
            return False, "not_configured"
        key = self.object_key(category, rel_path)
        try:
            size = os.path.getsize(local_path)
            client = self._client()
            client.upload_file(local_path, self.bucket, key)
            head = client.head_object(Bucket=self.bucket, Key=key)
            if int(head.get("ContentLength", 0)) != size:
                return False, "size_mismatch"
            return True, key
        except Exception as exc:
            logger.error("S3 upload failed for %s: %s", local_path, exc)
            return False, str(exc)

    def delete_object(self, category: str, rel_path: str) -> bool:
        if not self.is_configured:
            return False
        key = self.object_key(category, rel_path)
        try:
            self._client().delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            logger.error("S3 delete failed for %s: %s", key, exc)
            return False

    def list_objects(self, category: str = "") -> list:
        """List remote objects as {category, rel_path, size, modified_ts}."""
        if not self.is_configured:
            return []
        prefix = self.object_key(category, "") if category else (self.prefix + "/" if self.prefix else "")
        out = []
        try:
            client = self._client()
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    key = obj.get("Key") or ""
                    if not key or key.endswith("/"):
                        continue
                    rel = key
                    cat = category
                    if self.prefix and key.startswith(self.prefix + "/"):
                        rel = key[len(self.prefix) + 1 :]
                    if category:
                        if not rel.startswith(category + "/"):
                            continue
                        rel = rel[len(category) + 1 :]
                    elif "/" in rel:
                        cat, rel = rel.split("/", 1)
                    else:
                        continue
                    modified = obj.get("LastModified")
                    ts = modified.timestamp() if modified else 0
                    out.append(
                        {
                            "category": cat,
                            "rel_path": rel.replace("\\", "/"),
                            "size": int(obj.get("Size") or 0),
                            "modified_ts": ts,
                        }
                    )
        except Exception as exc:
            logger.error("S3 list_objects failed: %s", exc)
        return out

    def fetch_usage(self) -> dict:
        """Approximate bucket usage under prefix (bytes)."""
        if not self.is_configured:
            return {}
        total = sum(o.get("size", 0) for o in self.list_objects())
        return {"ok": True, "used_bytes": total, "used_gb": round(total / (1024**3), 3)}

    def test_connection(self) -> dict:
        if not self.is_configured:
            return {"ok": False, "error": "Remote storage is not fully configured"}
        probe_key = self.object_key("_probe", ".basebuddy_write_test")
        try:
            client = self._client()
            client.put_object(Bucket=self.bucket, Key=probe_key, Body=b"ok")
            client.delete_object(Bucket=self.bucket, Key=probe_key)
            return {
                "ok": True,
                "bucket": self.bucket,
                "provider": self.provider,
                "endpoint": self.endpoint_url or "(default AWS)",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def mask_secret(secret: str) -> str:
        if not secret:
            return ""
        if len(secret) <= 4:
            return "****"
        return secret[:2] + "****" + secret[-2:]

    def public_settings(self) -> dict:
        return {
            "REMOTE_STORAGE_ENABLED": self.enabled,
            "REMOTE_STORAGE_PROVIDER": self.provider,
            "REMOTE_BUCKET": self.bucket,
            "REMOTE_REGION": self.region,
            "REMOTE_ENDPOINT": self.endpoint_url,
            "REMOTE_PREFIX": self.prefix,
            "REMOTE_ACCESS_KEY": self.access_key,
            "REMOTE_SECRET_KEY_SET": bool(self.secret_key),
            "REMOTE_SECRET_KEY_MASK": self.mask_secret(self.secret_key),
        }
