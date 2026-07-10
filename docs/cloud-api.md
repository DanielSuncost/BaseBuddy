# BaseBuddy Cloud API

OpenAPI spec: [cloud-api.openapi.yaml](./cloud-api.openapi.yaml)

OSS HTTP client: `core/inference/cloud/client.py`

## Authentication

```http
Authorization: Bearer bb_live_xxxxxxxx
```

Optional header linking usage to a self-hosted install:

```http
X-Instance-Id: 550e8400-e29b-41d4-a716-446655440000
```

Configure in `.env`:

```bash
INFERENCE_MODE=cloud
INFERENCE_CLOUD_ENDPOINT=https://api.basebuddy.io/v1
INFERENCE_CLOUD_API_KEY=bb_live_xxxxxxxx
```

## Privacy defaults

- **`/inference/*`**: frames are processed ephemerally unless `X-Retain-Frames: true` (not recommended).
- **`/training/*`**: images are uploaded explicitly by the user from gallery labels or scene baselines.
- **Account delete**: removes models, datasets, and training artifacts.

## Local status

```bash
curl http://localhost:5000/api/inference/status
curl http://localhost:5000/health
```

## Error codes

| HTTP | code | Meaning |
|------|------|---------|
| 401 | invalid_api_key | Missing or bad key |
| 402 | quota_exceeded | Plan limit |
| 429 | rate_limited | Too many requests |
| 503 | gpu_unavailable | Retry later |

## Device authorization (in-app)

1. `POST /auth/device/code` → user opens verification URL
2. Poll `POST /auth/device/token`
3. Store refresh token locally in `config.txt` (never commit)

Device endpoints are part of the cloud service; not yet implemented in the OSS client.
