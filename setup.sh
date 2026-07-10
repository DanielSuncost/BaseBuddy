#!/bin/bash

# BaseBuddy Setup Script
# Quick setup for new installations

set -e  # Exit on error

echo "========================================="
echo "🏠 BaseBuddy Setup"
echo "========================================="
echo

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is not installed${NC}"
    echo "Please install Python 3.8 or higher"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d " " -f 2)
echo -e "${GREEN}✅ Python $PYTHON_VERSION found${NC}"
echo

# Create virtual environment
echo -e "${YELLOW}Creating virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment created${NC}"
else
    echo -e "${YELLOW}⚠️  Virtual environment already exists${NC}"
fi
echo

# Activate virtual environment
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate
echo -e "${GREEN}✅ Virtual environment activated${NC}"
echo

# Upgrade pip
echo -e "${YELLOW}Upgrading pip...${NC}"
pip install --upgrade pip --quiet
echo -e "${GREEN}✅ pip upgraded${NC}"
echo

# Install requirements
echo -e "${YELLOW}Installing dependencies...${NC}"
echo "This may take several minutes..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet
    echo -e "${GREEN}✅ Dependencies installed${NC}"
else
    echo -e "${RED}❌ requirements.txt not found${NC}"
    exit 1
fi
echo

# Create necessary directories
echo -e "${YELLOW}Creating directories...${NC}"
mkdir -p logs
mkdir -p recordings
mkdir -p gallery
mkdir -p timelapse_output
mkdir -p stills
mkdir -p media
mkdir -p backups
echo -e "${GREEN}✅ Directories created${NC}"
echo

# Check for configuration (.env preferred; config.txt also supported)
echo -e "${YELLOW}Checking configuration...${NC}"
if [ -f ".env" ]; then
    echo -e "${GREEN}✅ .env exists${NC}"
elif [ -f "config.txt" ]; then
    echo -e "${GREEN}✅ config.txt exists${NC}"
else
    if [ -f "env.example" ]; then
        cp env.example .env
        echo -e "${GREEN}✅ Created .env from env.example${NC}"
        echo -e "${RED}⚠️  IMPORTANT: Edit .env with your camera URLs!${NC}"
        echo "   nano .env"
    elif [ -f "config.example.txt" ]; then
        cp config.example.txt config.txt
        echo -e "${GREEN}✅ Created config.txt from config.example.txt${NC}"
        echo -e "${RED}⚠️  IMPORTANT: Edit config.txt with your camera URLs!${NC}"
        echo "   nano config.txt"
    else
        echo -e "${RED}❌ No env.example or config.example.txt found${NC}"
        exit 1
    fi
fi
echo

# Download AI model (optional)
echo -e "${YELLOW}Checking AI model...${NC}"
if [ ! -f "yolov8n.pt" ] && [ ! -f "models/yolov8n.pt" ]; then
    echo -e "${YELLOW}AI model not found. Download now? (y/n)${NC}"
    read -r DOWNLOAD_MODEL
    if [[ "$DOWNLOAD_MODEL" == "y" || "$DOWNLOAD_MODEL" == "Y" ]]; then
        echo "Downloading yolov8n.pt..."
        python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" || true
        echo -e "${GREEN}✅ AI model downloaded${NC}"
    else
        echo -e "${YELLOW}⚠️  Skipping AI model download${NC}"
        echo "   Detection will download it automatically on first run"
    fi
else
    echo -e "${GREEN}✅ AI model found${NC}"
fi
echo

echo "========================================="
echo -e "${GREEN}✅ Setup Complete!${NC}"
echo "========================================="
echo
echo "Next steps:"
echo "1. Edit configuration:"
echo "   nano .env    # or config.txt"
echo
echo "2. Add your camera URLs (in .env):"
echo "   CAM1=rtsp://user:pass@192.168.1.100:554/stream1"
echo
echo "3. Start BaseBuddy:"
echo "   ./run.sh"
echo
echo "4. Access the web interface:"
echo "   http://localhost:5000"
echo
echo "For more information, see:"
echo "  - README.md"
echo "  - CONTRIBUTING.md"
echo "  - docs/OPTIONAL_DEPS.md"
echo
echo "========================================="
