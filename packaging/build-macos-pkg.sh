#!/bin/bash
# Build a macOS .pkg installer for Lab Screenshot Bot
# Run this on a Mac to create the installer package
#
# Usage: ./packaging/build-macos-pkg.sh
# Output: packaging/dist/LabScreenshotBot.pkg

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('$PROJECT_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || echo "0.1.0")

echo "========================================="
echo "  Building Lab Screenshot Bot v$VERSION"
echo "  macOS .pkg installer"
echo "========================================="
echo ""

# Create build directories
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
STAGING="$BUILD_DIR/staging"
SCRIPTS="$BUILD_DIR/scripts"

rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$STAGING/usr/local/lib/lab-screenshot"
mkdir -p "$SCRIPTS"
mkdir -p "$DIST_DIR"

# Copy project files
echo "Copying project files..."
cp -r "$PROJECT_DIR/lab_screenshot" "$STAGING/usr/local/lib/lab-screenshot/"
cp "$PROJECT_DIR/pyproject.toml" "$STAGING/usr/local/lib/lab-screenshot/"
cp "$PROJECT_DIR/README.md" "$STAGING/usr/local/lib/lab-screenshot/"
cp "$PROJECT_DIR/install.sh" "$STAGING/usr/local/lib/lab-screenshot/"

# Create the postinstall script that runs after pkg extraction
cat > "$SCRIPTS/postinstall" << 'POSTINSTALL'
#!/bin/bash
# Post-install script — runs after files are copied

INSTALL_DIR="/usr/local/lib/lab-screenshot"
VENV_DIR="$INSTALL_DIR/venv"
BIN_LINK="/usr/local/bin/lab-screenshot"

echo "Setting up Lab Screenshot Bot..."

# Create virtual environment
if [ ! -d "$VENV_DIR" ]; then
    /usr/bin/python3 -m venv "$VENV_DIR"
fi

# Install package in venv
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -e "$INSTALL_DIR" -q

# Install Playwright Chromium
"$VENV_DIR/bin/python3" -m playwright install chromium

# Create symlink so 'lab-screenshot' works from anywhere
cat > "$BIN_LINK" << 'LAUNCHER'
#!/bin/bash
# Lab Screenshot Bot launcher
INSTALL_DIR="/usr/local/lib/lab-screenshot"
source "$INSTALL_DIR/venv/bin/activate"
exec lab-screenshot "$@"
LAUNCHER
chmod +x "$BIN_LINK"

echo ""
echo "Lab Screenshot Bot installed successfully!"
echo "Run: lab-screenshot app"
echo "Then open: http://localhost:8384"

exit 0
POSTINSTALL
chmod +x "$SCRIPTS/postinstall"

# Create uninstall script (included in the package for reference)
cat > "$STAGING/usr/local/lib/lab-screenshot/uninstall.sh" << 'UNINSTALL'
#!/bin/bash
# Uninstall Lab Screenshot Bot
echo "Removing Lab Screenshot Bot..."
rm -f /usr/local/bin/lab-screenshot
rm -rf /usr/local/lib/lab-screenshot
echo "Done. Lab Screenshot Bot has been removed."
UNINSTALL
chmod +x "$STAGING/usr/local/lib/lab-screenshot/uninstall.sh"

# Build the .pkg
echo ""
echo "Building .pkg installer..."
pkgbuild \
    --root "$STAGING" \
    --scripts "$SCRIPTS" \
    --identifier "com.okta.lab-screenshot" \
    --version "$VERSION" \
    --install-location "/" \
    "$DIST_DIR/LabScreenshotBot-$VERSION.pkg"

echo ""
echo "========================================="
echo "  Build complete!"
echo "========================================="
echo ""
echo "Installer: $DIST_DIR/LabScreenshotBot-$VERSION.pkg"
echo ""
echo "To install: double-click the .pkg file"
echo "To uninstall: sudo /usr/local/lib/lab-screenshot/uninstall.sh"
echo ""
