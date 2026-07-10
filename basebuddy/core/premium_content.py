"""
Marketing / help copy for BaseBuddy Cloud (safe to ship in open source).

Dynamic billing URLs and live subscription status come from basebuddy_premium (private).
"""

MANAGED_CLOUD_TAGLINE = (
    "Managed cloud storage for your cameras — one API key, no bucket setup."
)

MANAGED_CLOUD_FEATURES = [
    "Rolling cloud buffer — old files drop off as new ones arrive",
    "One API key, no S3/R2 setup",
    "Quota enforced on our servers",
]

MANAGED_CLOUD_INFERENCE_FEATURES = [
    "Everything in Cloud Storage",
    "Cloud GPU when local GPU is busy",
    "Hybrid: local first, cloud overflow",
]

LOST_KEY_HELP_OSS = (
    "API keys are shown once at signup. Sign in to your BaseBuddy account to rotate "
    "the key, or email support from the address on your subscription."
)

PRICING_SUMMARY_OSS = (
    "Pick a tier by how much cloud storage you need and how long you want the rolling buffer."
)

# Example tiers — override via Stripe / premium package in production.
PRICING_TIERS = [
    {
        "id": "starter",
        "label": "Starter",
        "cloud_storage_gb": 50,
        "cloud_buffer_days": 30,
        "storage_only_usd": 12,
        "with_inference_usd": 29,
        "cameras_hint": "Up to 4 cameras",
    },
    {
        "id": "pro",
        "label": "Pro",
        "cloud_storage_gb": 200,
        "cloud_buffer_days": 60,
        "storage_only_usd": 29,
        "with_inference_usd": 59,
        "cameras_hint": "Up to 10 cameras",
        "featured": True,
    },
    {
        "id": "studio",
        "label": "Studio",
        "cloud_storage_gb": 1000,
        "cloud_buffer_days": 90,
        "storage_only_usd": 79,
        "with_inference_usd": 149,
        "cameras_hint": "Up to 24 cameras",
    },
]

CLOUD_BUFFER_EXPLAIN = [
    "Your plan includes cloud storage (GB) and a rolling buffer (days).",
    "Files stay in cloud for up to N days, then delete automatically — making room for new uploads.",
    "Your server keeps a short local cache (step 3 below); that is separate from the cloud plan.",
]

ARTIFACT_SIZE_HINTS = [
    {"type": "Detection still", "size": "~150 KB / event"},
    {"type": "Timelapse still (1080p)", "size": "~350 KB / frame"},
    {"type": "Recording", "size": "~1.1 GB / cam / day @ 2.5 Mbps"},
]

# Legacy aliases for premium_hooks
QUOTA_POLICY = CLOUD_BUFFER_EXPLAIN
MANAGED_CLOUD_HOW_IT_WORKS = CLOUD_BUFFER_EXPLAIN
