#!/bin/bash
#
# BaseBuddy production launcher
# - Stops stale instances (process + port)
# - Loads config.txt / .env
# - Activates venv
# - Optional: --safe, --limit N, background
#
# Usage:
#   ./run.sh                 # foreground
#   ./run.sh background      # detached (logs/output.log, .pid)
#   ./run.sh --safe          # low-resource mode
#   ./run.sh --limit 2       # cap active cameras
#   ./stop.sh                # stop all instances

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKGROUND=0
SAFE_MODE=0
CAM_LIMIT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        background|--background|-d)
            BACKGROUND=1
            shift
            ;;
        --safe)
            SAFE_MODE=1
            shift
            ;;
        --limit)
            CAM_LIMIT_OVERRIDE="${2:?Usage: $0 [--safe] [--limit N] [background]}"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--safe] [--limit N] [background]"
            echo "  Stop stale BaseBuddy processes, then start main.py."
            echo "  background  — run detached (nohup, .pid, logs/output.log)"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Unknown option: $1 (try --help)${NC}" >&2
            shift
            ;;
    esac
done

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}BaseBuddy${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Virtual environment
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    if [[ -f "venv/bin/activate" ]]; then
        echo -e "${YELLOW}Activating venv...${NC}"
        # shellcheck disable=SC1091
        source venv/bin/activate
    elif [[ -f ".venv/bin/activate" ]]; then
        echo -e "${YELLOW}Activating .venv...${NC}"
        # shellcheck disable=SC1091
        source .venv/bin/activate
    else
        echo -e "${YELLOW}No venv found — using system Python${NC}"
        echo -e "${YELLOW}Create one: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt${NC}"
    fi
else
    echo -e "${GREEN}Using venv: $VIRTUAL_ENV${NC}"
fi

if [[ ! -f "main.py" ]]; then
    echo -e "${RED}main.py not found in $SCRIPT_DIR${NC}"
    exit 1
fi

# Config / env
if [[ -f "config.txt" ]]; then
    echo -e "${YELLOW}Loading config.txt...${NC}"
    set -a
    # shellcheck disable=SC1091
    source config.txt
    set +a
fi
if [[ -f ".env" ]]; then
    echo -e "${YELLOW}Loading .env...${NC}"
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

export BASEBUDDY_REPO_ROOT="$SCRIPT_DIR"
export BASEBUDDY_APP_ROOT="$SCRIPT_DIR/basebuddy"
export FLASK_ENV="${FLASK_ENV:-production}"
export FLASK_APP=main.py
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-5000}"

if [[ -n "$CAM_LIMIT_OVERRIDE" ]]; then
    export MAX_ACTIVE_CAMERAS="$CAM_LIMIT_OVERRIDE"
fi
if [[ "$SAFE_MODE" -eq 1 ]]; then
    export BASEBUDDY_SAFE_MODE=1
    export MAX_ACTIVE_CAMERAS="${MAX_ACTIVE_CAMERAS:-2}"
    echo -e "${YELLOW}Safe mode: max ${MAX_ACTIVE_CAMERAS} camera(s), throttled AI${NC}"
else
    unset BASEBUDDY_SAFE_MODE 2>/dev/null || true
fi

# Optional dependency check (warn only)
if ! python -c "import flask, cv2, numpy" 2>/dev/null; then
    echo -e "${RED}Missing core dependencies. Run: pip install -r requirements.txt${NC}"
    exit 1
fi
python -c "import ultralytics, torch" 2>/dev/null || \
    echo -e "${YELLOW}Optional: ultralytics/torch not installed — detection may be limited${NC}"

# GPU library paths (dynamic site-packages)
VENV_SITE="$(python -c 'import sys; print(next((p for p in sys.path if p.endswith("site-packages")), ""))' 2>/dev/null || true)"
if [[ -n "$VENV_SITE" && -d "$VENV_SITE" ]]; then
    for sub in tensorrt_libs nvidia/cudnn/lib; do
        if [[ -d "$VENV_SITE/$sub" ]]; then
            export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:$VENV_SITE/$sub"
        fi
    done
    LIBDEVICE_FILE="$VENV_SITE/triton/backends/nvidia/lib/libdevice.10.bc"
    if [[ -f "$LIBDEVICE_FILE" ]]; then
        LIBDEVICE_DIR="$(dirname "$LIBDEVICE_FILE")"
        export XLA_FLAGS="--xla_gpu_cuda_data_dir=$(readlink -f "$LIBDEVICE_DIR") --xla_gpu_force_compilation_parallelism=1"
        export TF_XLA_FLAGS=""
        [[ ! -f "$SCRIPT_DIR/libdevice.10.bc" ]] && ln -sf "$LIBDEVICE_FILE" "$SCRIPT_DIR/libdevice.10.bc" 2>/dev/null || true
    else
        export TF_XLA_FLAGS="--tf_xla_enable_xla_devices=false"
    fi
fi

# Stop stale processes — only main.py instances running from THIS repo
echo -e "${YELLOW}Stopping existing BaseBuddy processes...${NC}"
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
    OLD_PID="$(cat .pid 2>/dev/null || true)"
    if [[ -n "$OLD_PID" ]] && ps -p "$OLD_PID" >/dev/null 2>&1; then
        echo -e "${YELLOW}  Stopping previous background PID $OLD_PID${NC}"
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f .pid
fi

sleep 1
echo -e "${GREEN}Cleanup complete${NC}"
mkdir -p logs

echo ""
echo -e "${GREEN}Host:${NC} $HOST  ${GREEN}Port:${NC} $PORT  ${GREEN}Env:${NC} $FLASK_ENV"
echo -e "${GREEN}Web UI:${NC} http://127.0.0.1:${PORT}"
echo -e "${GREEN}Health:${NC} http://127.0.0.1:${PORT}/health"
echo -e "${GREEN}Logs:${NC} logs/basebuddy.log  (background: logs/output.log)"
echo ""

if [[ "$BACKGROUND" -eq 1 ]]; then
    echo -e "${YELLOW}Starting in background...${NC}"
    nohup python main.py >> logs/output.log 2>&1 &
    echo $! > .pid
    echo -e "${GREEN}Started PID $(cat .pid)${NC}"
    echo -e "${YELLOW}  tail -f logs/output.log${NC}"
    echo -e "${YELLOW}  ./stop.sh${NC}"
else
    echo -e "${GREEN}Starting (Ctrl+C to stop)...${NC}"
    echo ""
    exec python main.py
fi
