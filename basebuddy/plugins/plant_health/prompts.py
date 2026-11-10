"""Structured prompts for plant vision analysis."""

# OSS generic prompt — works with any OpenAI-compatible vision model.
GENERIC_OSS_PROMPT = """You are a horticulture assistant analyzing a plant photograph from a fixed monitoring camera.

Return ONLY valid JSON (no markdown) with these keys:
- health_score: integer 0-100 (100 = thriving)
- species_guess: string (best guess if unknown say "unknown")
- leaf_condition: one of "healthy", "yellowing", "browning", "wilting", "mixed", "unclear"
- visible_issues: array of short strings (pests, nutrient deficiency, overwatering signs, etc.)
- water_stress: boolean
- growth_stage: one of "seedling", "vegetative", "flowering", "fruiting", "dormant", "unknown"
- recommendations: array of 1-3 actionable care tips for the next 24-48 hours
- summary: one sentence plain-language status

Be conservative — if the image is unclear, lower confidence in issues and say so in summary."""

# Premium pipeline uses species-specific prompts after classifier (see premium package).
PREMIUM_PROMPT_NOTE = (
    "BaseBuddy Cloud uses a species classifier plus tailored prompts per plant "
    "for higher accuracy on watering, pests, and nutrient issues."
)

def build_multi_angle_prompt(brief: dict) -> str:
    """Qualitative pass over one plant seen from several cameras plus its numbers."""
    import json
    packet = json.dumps(brief or {}, default=str)
    return (
        "You are a horticulture assistant. The user monitors ONE plant with one or more "
        "fixed cameras. Photos are labeled by camera. A quantitative brief follows the photos.\n\n"
        "Return ONLY valid JSON (no markdown) with these keys:\n"
        "- health_score: integer 0-100 for the plant as a whole\n"
        "- agreement: one of \"aligned\", \"split\", \"single\"\n"
        "- alert: boolean, true only when the plant itself needs attention, not when one noisy angle disagrees\n"
        "- angle_notes: array of {\"camera\": string, \"observation\": string}\n"
        "- leaf_condition: one of \"healthy\", \"yellowing\", \"browning\", \"wilting\", \"mixed\", \"unclear\"\n"
        "- visible_issues: array of short strings\n"
        "- water_stress: boolean\n"
        "- recommendations: array of 1-3 care actions for the next 24-48 hours\n"
        "- summary: one sentence that a person can act on\n\n"
        "Use the brief's rates and the images together. Do not invent measurements.\n\n"
        "Quantitative brief:\n"
        + packet
    )


def build_oss_prompt(species_hint: str = "") -> str:
    if species_hint and species_hint.strip().lower() not in ("", "unknown", "other"):
        return (
            GENERIC_OSS_PROMPT
            + f"\n\nThe user believes this plant is: {species_hint.strip()}. "
            "Factor that into species_guess and recommendations."
        )
    return GENERIC_OSS_PROMPT
