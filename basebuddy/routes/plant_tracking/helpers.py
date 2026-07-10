"""Configuration constants, path-safety helpers and the lazy SAM loader."""

# Configuration
STILLS_DIR = "stills"
PROMPT_CONFIG_DIR = "sam_prompt_configs"
MASKS_DIR = "plant_segmentation_results"
SAM_CHECKPOINT = "sam_vit_b_01ec64.pth"


def _safe_seg(value) -> str | None:
    """Validate a single path segment (camera id or filename) from a request.

    Rejects path separators and ``..`` so user input can't escape the
    configured stills/mask directories.
    """
    from basebuddy.core.upload_safety import safe_basename

    if value is None:
        return None
    return safe_basename(str(value))


def _safe_still_path(image_path: str) -> str | None:
    """Resolve a client-supplied stills path, ensuring it stays under STILLS_DIR."""
    from basebuddy.core.upload_safety import resolve_under_dir

    if not image_path:
        return None
    rel = str(image_path).lstrip("/")
    if rel.startswith(STILLS_DIR + "/"):
        rel = rel[len(STILLS_DIR) + 1:]
    resolved = resolve_under_dir(STILLS_DIR, rel)
    if resolved and resolved.is_file():
        return str(resolved)
    return None

# Global SAM predictor (lazy loaded via inference provider)
def get_sam_predictor():
    """Lazy load SAM predictor on GPU (delegates to core.inference)."""
    from basebuddy.core.inference.local.sam_segmentation import get_sam_predictor as _load_sam
    return _load_sam()
