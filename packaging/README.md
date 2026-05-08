# Packaging Lab Screenshot Bot

## macOS .pkg Installer

### Building the installer

Run this **on a Mac** (requires `pkgbuild` which is part of Xcode Command Line Tools):

```bash
cd lab-screenshot
./packaging/build-macos-pkg.sh
```

Output: `packaging/dist/LabScreenshotBot-{version}.pkg`

### What the installer does

1. Copies the project to `/usr/local/lib/lab-screenshot/`
2. Creates a Python virtual environment with all dependencies
3. Downloads the Playwright Chromium browser
4. Creates a launcher at `/usr/local/bin/lab-screenshot` so the command works from anywhere

### Installing

Double-click the `.pkg` file and follow the wizard. Requires admin password (installs to `/usr/local/`).

After install:
```bash
lab-screenshot app
```

### Uninstalling

```bash
sudo /usr/local/lib/lab-screenshot/uninstall.sh
```

### Requirements on the target Mac

- macOS 12+ (Monterey or later)
- Python 3.10+ (comes with Xcode Command Line Tools, or install via `brew install python3`)
- ~500MB disk space (mostly Playwright Chromium)

### Signing and notarization (optional)

To sign the package for distribution without Gatekeeper warnings:

```bash
# Sign with a Developer ID Installer certificate
productsign --sign "Developer ID Installer: Your Name (TEAMID)" \
  packaging/dist/LabScreenshotBot.pkg \
  packaging/dist/LabScreenshotBot-signed.pkg

# Notarize with Apple
xcrun notarytool submit packaging/dist/LabScreenshotBot-signed.pkg \
  --apple-id you@email.com \
  --team-id TEAMID \
  --password @keychain:AC_PASSWORD \
  --wait

# Staple the notarization ticket
xcrun stapler staple packaging/dist/LabScreenshotBot-signed.pkg
```
