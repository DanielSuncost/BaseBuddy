"""
Persist key=value pairs into project config.txt (export KEY="value" lines).

Used by the storage policy UI and other tools that need to update env-backed
settings without rewriting the entire file by hand.
"""
import os
import shlex
from typing import Dict


def format_config_export(key: str, value: str) -> str:
    """Return a shell-safe `export KEY=...` line (JSON-safe quoting)."""
    return f"export {key}={shlex.quote(value)}\n"


def parse_export_value(raw: str) -> str:
    """Strip shell-style quoting from the value portion of an export line."""
    value = raw.strip()
    if not value:
        return value
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        parts = shlex.split(value, posix=True)
        if parts:
            return parts[0]
    except ValueError:
        pass
    return value


def config_txt_path(project_root: str) -> str:
    return os.path.join(project_root, "config.txt")


def upsert_config_exports(project_root: str, updates: Dict[str, str]) -> None:
    """
    Merge *updates* into config.txt: replace existing export lines for the same
    keys, append keys that are not yet present. Other lines are preserved.
    """
    path = config_txt_path(project_root)
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()

    keys_done: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export ") and "=" in stripped:
            body = stripped[7:]
            key = body.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(format_config_export(key, updates[key]))
                keys_done.add(key)
                continue
        new_lines.append(line)

    for key, raw in updates.items():
        if key in keys_done:
            continue
        new_lines.append(format_config_export(key, raw))

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)
