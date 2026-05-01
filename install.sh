#!/bin/bash
# lab-screenshot — quick install for macOS/Linux
# Usage: curl -sSL https://raw.githubusercontent.com/joevanhorn/lab-screenshot/main/install.sh | bash

set -e

echo "=== Installing lab-screenshot ==="

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3.10+ required. Install from https://python.org"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python: $PY_VERSION"

# Clone or update
INSTALL_DIR="$HOME/lab-screenshot"
if [ -d "$INSTALL_DIR" ]; then
    echo "  Updating existing install..."
    cd "$INSTALL_DIR" && git pull
else
    echo "  Cloning repo..."
    git clone https://github.com/joevanhorn/lab-screenshot.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Install package
echo "  Installing Python package..."
pip3 install -e . 2>/dev/null || pip3 install --user -e .

# Install Playwright + Chromium
echo "  Installing Playwright + Chromium..."
python3 -m playwright install chromium

# Verify
echo ""
echo "=== Installation complete ==="
echo ""
lab-screenshot --help 2>/dev/null || python3 -m lab_screenshot.cli --help
echo ""
echo "Quick start:"
echo "  cd $INSTALL_DIR"
echo "  lab-screenshot record your-guide.md --org https://your-lab-url --setup"
echo ""
echo "Set your LLM API key first:"
echo "  export ANTHROPIC_API_KEY=your-key"
echo "  # OR"
echo "  export LITELLM_API_BASE=https://your-proxy  LITELLM_API_KEY=your-key"
