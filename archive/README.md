# Archive

This directory holds **deprecated code kept for historical reference only**.

| File | Status |
|------|--------|
| `main_legacy.py` | Original ~12k-line monolith. **Not used at runtime.** No imports from the modular app. |

The active application is:

- **Entry:** `main.py` (repo root) → `basebuddy/main.py`
- **Structure:** documented in [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)

`main_legacy.py` is gitignored in OSS publishes (see root `.gitignore`). Safe to delete locally once you no longer need the reference.
