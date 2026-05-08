#!/bin/bash
# Lab Screenshot Bot — One-line installer
# Usage: curl -sSL https://raw.githubusercontent.com/joevanhorn/lab-screenshot/main/install.sh | bash

set -e

echo ""
echo "========================================="
echo "  Lab Screenshot Bot — Installer"
echo "========================================="
echo ""

# Check Python version
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3.10+ is required but not installed."
    echo "  macOS:   brew install python3"
    echo "  Ubuntu:  sudo apt install python3 python3-pip"
    echo "  Windows: https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "ERROR: Python 3.10+ required. You have Python $PY_VERSION."
    exit 1
fi
echo "✓ Python $PY_VERSION"

# Check git
if ! command -v git &>/dev/null; then
    echo "ERROR: git is required but not installed."
    exit 1
fi
echo "✓ git available"

# Clone or update
INSTALL_DIR="$HOME/lab-screenshot"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo ""
    echo "Updating existing installation..."
    cd "$INSTALL_DIR" && git pull --ff-only
else
    echo ""
    echo "Downloading lab-screenshot..."
    git clone https://github.com/joevanhorn/lab-screenshot.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

# Activate venv and install
echo ""
echo "Installing dependencies..."
source "$INSTALL_DIR/venv/bin/activate"
pip install --upgrade pip -q
pip install -e . -q

# Install Playwright Chromium
echo ""
echo "Installing Playwright Chromium browser..."
python3 -m playwright install chromium
python3 -m playwright install-deps chromium 2>/dev/null || true

# Create a launcher script in a common PATH location
LAUNCHER="$INSTALL_DIR/venv/bin/lab-screenshot"
if [ -f "$LAUNCHER" ]; then
    echo ""
    echo "✓ lab-screenshot command installed"
fi

# Deactivate venv
deactivate 2>/dev/null || true

echo ""
echo "========================================="
echo "  Installation complete!"
echo "========================================="
echo ""
echo "To launch the app:"
echo ""
echo "  cd $INSTALL_DIR"
echo "  source venv/bin/activate"
echo "  lab-screenshot app"
echo ""
echo "Then open http://localhost:8384 in your browser."
echo ""
echo "To update later:"
echo "  cd $INSTALL_DIR && git pull && source venv/bin/activate && pip install -e ."
echo ""
