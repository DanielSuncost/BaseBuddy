#!/bin/bash

# Create venv, install requirements, ensure runtime directories and a config file.

set -e  # Exit on error

echo "setup"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check Python version
echo -e "${YELLOW}python${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}python3 not found${NC}"
    echo "Python 3.10 or newer is required"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d " " -f 2)
echo -e "${GREEN}python $PYTHON_VERSION${NC}"
echo

# Create virtual environment
echo -e "${YELLOW}venv${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}venv created${NC}"
else
    echo -e "${YELLOW}venv already exists${NC}"
fi
echo

# Activate virtual environment
echo -e "${YELLOW}activate${NC}"
source venv/bin/activate
echo -e "${GREEN}venv active${NC}"
echo

# Upgrade pip
echo -e "${YELLOW}pip${NC}"
pip install --upgrade pip --quiet
echo -e "${GREEN}pip upgraded${NC}"
echo

# Install requirements
echo -e "${YELLOW}dependencies${NC}"
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
    echo -e "${GREEN}dependencies installed${NC}"
else
    echo -e "${RED}requirements.txt not found${NC}"
    exit 1
fi
echo

# Create necessary directories
echo -e "${YELLOW}directories${NC}"
mkdir -p logs
mkdir -p recordings
mkdir -p gallery
mkdir -p timelapse_output
mkdir -p stills
mkdir -p media
mkdir -p backups
echo -e "${GREEN}directories created${NC}"
echo

# Check for configuration (.env preferred; config.txt also supported)
echo -e "${YELLOW}config${NC}"
if [ -f ".env" ]; then
    echo -e "${GREEN}.env present${NC}"
elif [ -f "config.txt" ]; then
    echo -e "${GREEN}config.txt present${NC}"
else
    if [ -f "env.example" ]; then
        cp env.example .env
        echo -e "${GREEN}wrote .env from env.example${NC}"
        echo -e "${RED}edit .env and set CAM1, CAM2, ...${NC}"
    elif [ -f "config.example.txt" ]; then
        cp config.example.txt config.txt
        echo -e "${GREEN}wrote config.txt from config.example.txt${NC}"
        echo -e "${RED}edit config.txt and set camera URLs${NC}"
    else
        echo -e "${RED}env.example and config.example.txt are missing${NC}"
        exit 1
    fi
fi
echo

# Download AI model (optional)
echo -e "${YELLOW}weights${NC}"
if [ ! -f "yolov8n.pt" ] && [ ! -f "models/yolov8n.pt" ]; then
    echo -e "${YELLOW}yolov8n.pt not found. Download now? (y/n)${NC}"
    read -r DOWNLOAD_MODEL
    if [[ "$DOWNLOAD_MODEL" == "y" || "$DOWNLOAD_MODEL" == "Y" ]]; then
        echo "downloading yolov8n.pt"
        python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" || true
        echo -e "${GREEN}weights downloaded${NC}"
    else
        echo -e "${YELLOW}weights skipped; first run will download yolov8n.pt${NC}"
    fi
else
    echo -e "${GREEN}weights present${NC}"
fi
echo
echo "done"
echo "  edit .env   (CAM1=rtsp://user:pass@host:554/stream)"
echo "  ./run.sh"
echo "  http://127.0.0.1:5000"
