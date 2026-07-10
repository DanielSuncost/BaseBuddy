#!/usr/bin/env bash
# Create a single-commit main branch for first push to GitHub.
# Does NOT push — run: git push -u origin main --tags
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REMOTE_URL="${1:-https://github.com/YOUR_ORG/BaseBuddy.git}"

echo "== BaseBuddy — clean history prep =="
echo "Remote: $REMOTE_URL"
echo ""

# Block secrets / runtime blobs from being staged
bad=()
while IFS= read -r f; do
  case "$f" in
    config.txt|.env|*.db|*.pt|*.pth) bad+=("$f") ;;
  esac
done < <(git ls-files --others --exclude-standard 2>/dev/null; git diff --name-only 2>/dev/null)

for f in config.txt .env analytics.db; do
  if [[ -f "$f" ]] && ! git check-ignore -q "$f" 2>/dev/null; then
    echo "ERROR: $f exists and is not gitignored — fix .gitignore before publishing."
    exit 1
  fi
done

if [[ ${#bad[@]} -gt 0 ]]; then
  echo "ERROR: untracked sensitive/large files present: ${bad[*]}"
  exit 1
fi

echo "Smoke test…"
if [[ -x "$ROOT/venv/bin/python" ]]; then
  "$ROOT/venv/bin/python" scripts/smoke_test.py
else
  python3 scripts/smoke_test.py
fi

# Keep old history locally as master-backup (optional restore point)
if git show-ref --verify --quiet refs/heads/master; then
  git branch -f master-backup master
  echo "Saved old branch as master-backup"
fi

echo "Creating orphan main (no parent commits)…"
git checkout --orphan main

echo "Staging OSS tree…"
git add -A

echo "Staged file count: $(git diff --cached --name-only | wc -l)"
if git diff --cached --name-only | grep -qE '(^config\.txt$|^\.env$|\.db$|\.pt$)'; then
  echo "ERROR: refused to commit secrets or weights"
  git reset HEAD
  exit 1
fi

git commit -m "$(cat <<'EOF'
BaseBuddy 1.0.0 — initial public release

Modular self-hosted AI camera system: Flask backend, YOLO detection,
gallery labeling, Docker/CI, and plugin architecture.
EOF
)"

git tag -a v1.0.0 -m "BaseBuddy 1.0.0"

if git remote get-url origin &>/dev/null; then
  echo "Updating existing origin → $REMOTE_URL"
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

echo ""
echo "Done. Local branch: main (1 commit, tag v1.0.0)"
echo "Old history preserved on: master-backup"
echo ""
echo "When ready to publish:"
echo "  git push -u origin main --tags"
echo ""
echo "Optional: rename local folder BaseBuddyClean → BaseBuddy (outside git)"
