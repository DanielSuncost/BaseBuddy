"""Inference provider errors."""


class InferenceError(Exception):
    """Base inference error."""


class ResourceExhausted(InferenceError):
    """Local GPU/CPU resources unavailable."""


class CloudNotConfigured(InferenceError):
    """Cloud inference requested but API key or endpoint missing."""


class CloudQuotaExceeded(InferenceError):
    """Cloud usage quota exceeded."""
