"""Load, merge, and serialize per-service retention policy."""
import json
from typing import Any, Dict

from basebuddy.core.retention_defaults import DEFAULT_RETENTION_POLICY


def _coerce_days(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def normalize_retention_policy(raw: Any) -> Dict[str, Dict[str, int]]:
    """Merge user policy with defaults; ensure every known service is present."""
    base = {
        k: {"local_days": v["local_days"], "remote_days": v["remote_days"]}
        for k, v in DEFAULT_RETENTION_POLICY.items()
    }
    if not isinstance(raw, dict):
        return base
    for service, cfg in raw.items():
        if service not in base or not isinstance(cfg, dict):
            continue
        if "local_days" in cfg:
            base[service]["local_days"] = _coerce_days(cfg["local_days"], base[service]["local_days"])
        if "remote_days" in cfg:
            base[service]["remote_days"] = _coerce_days(cfg["remote_days"], base[service]["remote_days"])
    return base


def retention_policy_from_env(raw_json: str, legacy_retention_days: int) -> Dict[str, Dict[str, int]]:
    """Parse RETENTION_POLICY JSON; fall back recordings.local_days to RETENTION_DAYS."""
    if raw_json and raw_json.strip() not in ("", "{}", "null"):
        try:
            parsed = json.loads(raw_json)
            policy = normalize_retention_policy(parsed)
            if "recordings" in policy and legacy_retention_days > 0:
                if not parsed.get("recordings", {}).get("local_days"):
                    policy["recordings"]["local_days"] = legacy_retention_days
            return policy
        except json.JSONDecodeError:
            pass
    policy = normalize_retention_policy({})
    if legacy_retention_days > 0:
        policy["recordings"]["local_days"] = legacy_retention_days
    return policy


def retention_policy_to_json(policy: Dict[str, Dict[str, int]]) -> str:
    return json.dumps(normalize_retention_policy(policy), separators=(",", ":"))
