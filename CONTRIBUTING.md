# Contributing to BaseBuddy

Thanks for your interest in contributing.

## Repository layout

| Path | Purpose |
|------|---------|
| `basebuddy/` | Application source — all Python packages, templates, static assets |
| `docs/` | Architecture, security, optional dependencies |
| `scripts/` | Smoke tests and tooling |
| `archive/` | Deprecated legacy code (not imported at runtime) |

Runtime data (`recordings/`, `logs/`, `models/*.pt`, `.env`) lives at **repo root**, not inside `basebuddy/`.

## Application structure

- **`basebuddy/main.py`** – Application entry (services, camera init).
- **`basebuddy/app/`** – Flask app factory; registers blueprints.
- **`basebuddy/core/`** – Core API blueprints and services (metrics, storage, backup, health).
- **`basebuddy/pages/`** – One folder per UI page (routes + API). **Prefer new pages here.**
- **`basebuddy/modules/`** – Shared logic (camera, detection, database, config).
- **`basebuddy/routes/`** – Feature blueprints (cameras, people, multiview, …).
- **`basebuddy/plugins/`** – Optional features (home scenes, traffic analytics).

Path helpers: `basebuddy/core/paths.py` (`get_repo_root()`, `get_app_root()`).

## Adding a new page

1. Create `basebuddy/pages/<name>/` with `__init__.py`, `routes.py`, and `api.py`.
2. Register the blueprint in `basebuddy/pages/__init__.py`.
3. Add templates under `basebuddy/templates/` and static assets under `basebuddy/static/`.

## Configuration

Config is loaded from `.env` (see `env.example`) or `config.txt` (see `config.example.txt`). Do not commit secrets.

## Running

```bash
./run.sh              # production launcher
./run.sh --safe       # low-resource mode
./run.sh background   # detached
./stop.sh
python scripts/smoke_test.py
```

Docker: `docker compose up -d` (after copying `env.example` to `.env`).

## Premium / hosted code

Open-source builds use `core/premium_hooks.py` only. The `premium/` and `hosted/` directories are private overlays and are not part of the public artifact.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
