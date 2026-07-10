#!/bin/bash
#
# Stop all BaseBuddy instances (foreground, background, legacy).

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=5000
if [[ -f "config.txt" ]]; then
    set -a
    # shellcheck disable=SC1091
    source config.txt 2>/dev/null || true
    set +a
fi
if [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env 2>/dev/null || true
    set +a
fi
PORT="${PORT:-5000}"

echo -e "${YELLOW}Stopping BaseBuddy...${NC}"

# Only stop main.py instances running from THIS repo
for pid in $(pgrep -f "python[0-9.]* .*main\.py" 2>/dev/null || true); do
    if [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" == "$SCRIPT_DIR" ]]; then
        kill "$pid" 2>/dev/null || true
    fi
done

# Free only the configured port (other apps may own 5000/5001)
if lsof -Pi ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}  Freeing port $PORT${NC}"
    lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
fi

if [[ -f ".pid" ]]; then
    PID="$(cat .pid 2>/dev/null || true)"
    if [[ -n "$PID" ]] && ps -p "$PID" >/dev/null 2>&1; then
        echo -e "${YELLOW}  Stopping PID $PID${NC}"
        kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f .pid
fi

sleep 1
echo -e "${GREEN}BaseBuddy stopped${NC}"
