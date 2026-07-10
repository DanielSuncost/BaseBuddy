# Security

BaseBuddy is designed for **self-hosted** deployment on a trusted network (home lab, LAN). Treat internet-facing installs as **production** and harden accordingly.

## Authentication

- HTTP basic auth is **off by default** (`AUTH_ENABLE=false`).
- Enable for any host reachable beyond localhost:

```bash
AUTH_ENABLE=true
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong-password>
SECRET_KEY=<long-random-string>
```

- `/health` remains unauthenticated for load balancers and Docker health checks.

## Secrets and file permissions

- Never commit `config.txt`, `.env`, or camera RTSP URLs with credentials.
- Use `env.example` / `config.example.txt` placeholders only in the repo.
- Restrict config files on disk:

```bash
chmod 600 .env config.txt
```

- Rotate `SECRET_KEY`, storage keys, and cloud API keys if exposed.

## Network exposure

- Default bind: `0.0.0.0:5000` — restrict with firewall rules or reverse proxy (TLS termination recommended).
- With `FLASK_ENV=production`, cross-origin access is **disabled** unless you set `CORS_ORIGINS`:

```bash
# Only if you host the UI on a different origin (unusual for self-hosted)
CORS_ORIGINS=https://cameras.example.com,http://192.168.1.50:5000
```

- SocketIO follows the same rule: no wildcard CORS in production when `CORS_ORIGINS` is unset.

## File uploads

- User uploads are limited to **`/api/multiview/calibration/upload`** (checkerboard calibration images).
- Uploads are validated: numeric `camera_id`, jpg/png only, max 50 files, paths confined under `multiview/calibration_images/` (`core/upload_safety.py`).
- Global request body limit: 100 MB (`MAX_CONTENT_LENGTH` in app factory).

## Camera credentials

- RTSP URLs often embed usernames/passwords. Store them in `.env` or `config.txt` (both gitignored).
- Logs should not print full camera URLs; use masked values in UI where possible.

## Face recognition & privacy

- DeepFace / InsightFace process person crops locally. Disable ML stack if not needed (`pip install -r requirements-core.txt` only).
- Recordings and detection media live on disk under repo root; secure filesystem permissions and backups.

## Reporting vulnerabilities

Please report security issues privately to the maintainers rather than opening public GitHub issues with exploit details.
