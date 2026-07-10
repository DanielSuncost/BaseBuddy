"""OSS plant vision analyzer — any OpenAI-compatible vision API."""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, Optional

from basebuddy.plugins.plant_health.prompts import build_oss_prompt

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"summary": text[:500], "health_score": None, "parse_error": True}


class OSSPlantAnalyzer:
    """Send plant image to user-configured vision API."""

    name = "oss"

    def is_configured(self) -> bool:
        from basebuddy.modules.config import env_live
        return bool(env_live("PLANT_VISION_API_KEY") and env_live("PLANT_VISION_API_URL"))

    def analyze(
        self,
        image_bytes: bytes,
        *,
        species_hint: str = "",
        monitor: Optional[dict] = None,
    ) -> Dict[str, Any]:
        from basebuddy.modules.config import env_live

        api_url = (env_live("PLANT_VISION_API_URL") or "").strip()
        api_key = (env_live("PLANT_VISION_API_KEY") or "").strip()
        model = (env_live("PLANT_VISION_MODEL") or "gpt-4o-mini").strip()

        if not api_url or not api_key:
            return {
                "ok": False,
                "error": "Configure PLANT_VISION_API_URL and PLANT_VISION_API_KEY in Plants settings",
                "health_score": None,
            }

        hint = species_hint or (monitor or {}).get("species_hint") or ""
        prompt = build_oss_prompt(hint)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 800,
        }

        try:
            import requests
            resp = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            body = resp.json()
            content = (
                body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            parsed = _extract_json(content)
            parsed["ok"] = True
            parsed["analyzer"] = self.name
            return parsed
        except Exception as exc:
            logger.warning("Plant vision API failed: %s", exc)
            return {"ok": False, "error": str(exc), "health_score": None, "analyzer": self.name}
