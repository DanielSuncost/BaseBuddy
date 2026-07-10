# Changelog

All notable changes to BaseBuddy are documented here.

## [1.0.0] — 2026-06-27

### Added

- **`basebuddy/` application tree** — OSS source separated from repo-root runtime data
- Central path resolution (`basebuddy/core/paths.py`, `get_stills_root()`)
- Modular pages: gallery, config, timelapse, recordings, storage policy, camera wall/detail, metrics
- Gallery **group modal** — track sequences and similar-position groups as image grids
- Gallery **training labels** — false positive, person name, corrected class, notes; export YOLO zip + JSON
- **False-positive ignore zones** — region-based ingest skip (IoU)
- Home scenes plugin (pantry/fridge MVP, ROI editor, camera preview picker)
- Inference provider abstraction (`core/inference/`) — local YOLO, cloud client stub
- Optional HTTP basic auth (`AUTH_ENABLE`)
- `/health` endpoint
- `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/OPTIONAL_DEPS.md`
- GitHub Actions CI (`scripts/smoke_test.py` + compile check)
- Split requirements: `requirements-core.txt` / `requirements-ml.txt`
- Docker: `Dockerfile`, `docker-compose.yml`, `Dockerfile.gpu`, `docker-compose.gpu.yml`
- Production `./run.sh` / `./stop.sh`
- `scripts/export_training.py` CLI
- Upload path validation (`core/upload_safety.py`)

### Changed

- Entry point: repo-root `main.py` shim → `basebuddy/main.py`
- Logging replaces ad-hoc `print()` in hot paths
- Gallery: dynamic camera filter, compact toolbar, lazy-loaded thumbnails, pagination (50–200/page)
- Stills/timelapse canonical path: repo-root `stills/` (multiview sync merges legacy paths)
- Production CORS: no wildcard when `FLASK_ENV=production` unless `CORS_ORIGINS` is set
- Docker image tag: `basebuddy:1.0.0`

### Removed

- Duplicate `web/static` and `web/templates` trees
- Dead routes: `routes/camera_wall.py`, `routes/timelapse.py`
- `run_new.sh`, unused `database_extensions.py`
- Legacy `main_legacy.py` archived (not imported)

### Security

- Document `chmod 600` for `.env` / `config.txt`
- Calibration upload: validated camera id, extension whitelist, path confinement, 50-file cap

## [Unreleased]

See git history on `main` for ongoing work.

## [0.1.0-alpha] — prior development

- Modular pages architecture, inference provider, home scenes, gallery export
- YOLOv8 detection, storage policy, backup/archive services
