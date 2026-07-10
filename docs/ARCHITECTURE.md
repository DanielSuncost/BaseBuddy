# BaseBuddy architecture

## Repository layout

```
/
├── basebuddy/          Application source (Python packages, templates, static)
├── archive/            Legacy reference only (not used at runtime)
├── docs/               User and developer documentation
├── scripts/            Maintenance and CI helpers
├── models/             Downloaded AI weights (gitignored)
├── recordings/         Runtime data (gitignored)
├── logs/               Runtime logs (gitignored)
├── main.py             Repo entry shim → basebuddy/main.py
├── run.sh / stop.sh    Production launcher scripts
├── env.example         Primary configuration template
└── docker-compose.yml
```

**Repo root** (`BASEBUDDY_REPO_ROOT`) holds configuration and runtime data.  
**Application root** (`BASEBUDDY_APP_ROOT`, usually `basebuddy/`) holds importable code and UI assets.

Path resolution is centralized in `basebuddy/core/paths.py`.

## Application layers

| Layer | Path | Role |
|-------|------|------|
| Entry | `main.py` (root + `basebuddy/main.py`) | Bootstrap paths, services, camera init |
| App factory | `basebuddy/app/` | Flask app, auth, blueprint registration |
| Pages | `basebuddy/pages/<name>/` | One blueprint per UI page (`routes.py`, `api.py`) |
| Core API | `basebuddy/core/api/` | Shared REST endpoints (gallery, storage, health, …) |
| Services | `basebuddy/core/services/` | Background jobs (backup, archive, retention) |
| Modules | `basebuddy/modules/` | Camera grabbers, detection, database, config |
| Routes | `basebuddy/routes/` | Legacy feature bundles (people, multiview, plant tracking) |
| Plugins | `basebuddy/plugins/` | Optional features (home scenes, traffic analytics) |
| Inference | `basebuddy/core/inference/` | Local YOLO/SAM + cloud/hybrid router |

## Request flow

```
HTTP / WebSocket
    → app.create_app()
        → core.api (REST)
        → plugins (optional features)
        → pages (HTML + page APIs)
        → routes (legacy blueprints)
        → app.auth (optional basic auth)
```

## Open source vs premium

- **OSS**: everything under `basebuddy/` plus docs and scripts at repo root.
- **Premium**: optional `basebuddy_premium` pip package wired through `core/premium_hooks.py`.
- **Hosted services** (`hosted/`, `premium/`): private repos; not part of the public artifact.

## Legacy

`archive/main_legacy.py` is the pre-modular monolith (~12k lines). It is **not imported** and **not required** to run the application. Kept locally under `archive/` for historical reference only (gitignored in OSS publishes).

## Adding a page

See [CONTRIBUTING.md](../CONTRIBUTING.md). Pattern: `pages/<name>/__init__.py`, `routes.py`, `api.py`, register in `pages/__init__.py`.
