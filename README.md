# lab-screenshot

Automated screenshot tool for Okta Admin Console lab guides. Write a markdown guide with `[SCREENSHOT: description]` markers, point it at your Okta org, and get back the same guide with real screenshots embedded as inline base64 images.

## Install

```bash
cd modules/lab-screenshot
pip install -e .
playwright install chromium
```

## Quick Start

```bash
# 1. Check what markers are in a guide
lab-screenshot check guides/my-guide.md

# 2. Process the guide (interactive — prompts for navigation at each marker)
lab-screenshot run guides/my-guide.md \
  --org https://your-org.okta.com \
  --username bot@your-org.com \
  --totp-secret YOUR_TOTP_SECRET

# 3. Or provide page paths upfront (non-interactive)
lab-screenshot run guides/my-guide.md \
  --org https://your-org.okta.com \
  --username bot@your-org.com \
  --totp-secret YOUR_TOTP_SECRET \
  --pages "/admin/apps/active,/admin/users,/admin/oauth2/as" \
  --no-prompt \
  --save-pngs
```

## Commands

### `lab-screenshot check <guide.md>`

Dry run — parse the guide and list all `[SCREENSHOT: ...]` markers without taking any screenshots.

### `lab-screenshot login`

Authenticate headlessly and save a browser session for later use.

```bash
lab-screenshot login \
  --org https://your-org.okta.com \
  --username bot@your-org.com \
  --totp-secret YOUR_TOTP_SECRET
```

### `lab-screenshot capture`

Take a single screenshot of an admin console page.

```bash
lab-screenshot capture \
  --org https://your-org.okta.com \
  --path /admin/dashboard \
  -o screenshot.png
```

### `lab-screenshot run <guide.md>`

Full pipeline: authenticate, navigate to each marker, capture, replace markers with base64 images, write output.

```bash
lab-screenshot run guide.md \
  --org https://your-org.okta.com \
  --username bot@your-org.com \
  --totp-secret YOUR_TOTP_SECRET \
  -o guide-with-screenshots.md \
  --save-pngs
```

**Navigation modes:**

| Flag | Behavior |
|------|----------|
| (default) | Interactive — prompts for URL path at each marker |
| `--pages "/path1,/path2,/path3"` | Pre-defined paths mapped to markers by index |
| `--no-prompt` | Capture whatever page is currently showing |

## Guide Format

Guides are plain markdown. Place `[SCREENSHOT: description]` markers where you want screenshots:

```markdown
# Lab: Create an Auth Server

1. Navigate to **Security > API > Authorization Servers**
2. Click **Add Authorization Server**
3. Fill in the name and audience
4. Click **Save**

[SCREENSHOT: Auth server settings page showing name and audience]

## Add Scopes

1. Click the **Scopes** tab
2. Add scopes: sfdc:read, sfdc:write, snow:read, snow:write, mcp:read

[SCREENSHOT: Scopes tab with all five scopes listed]
```

After processing, each marker becomes:

```markdown
![Auth server settings page showing name and audience](data:image/png;base64,iVBOR...)
```

## Authentication

The tool authenticates headlessly via the Okta authn API:
1. `POST /api/v1/authn` → session token
2. Cookie redirect → admin console OAuth
3. Browser TOTP MFA (if `--totp-secret` provided, otherwise prompts)
4. "Keep me signed in" auto-click

**Requires an Okta-mastered user** (not AD-sourced) with admin access.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--org` | (required) | Okta org URL |
| `--username` | (prompts) | Okta username |
| `--password` | (prompts) | Okta password |
| `--totp-secret` | (prompts for code) | TOTP shared secret for automated MFA |
| `-o, --output` | Overwrites input | Output file path |
| `--pages` | (interactive) | Comma-separated URL paths per marker |
| `--no-prompt` | false | Skip navigation prompts |
| `--save-pngs` | false | Save individual PNGs alongside output |
| `--width` | 1440 | Viewport width |
| `--height` | 900 | Viewport height |
| `--delay` | 2000 | Post-navigation delay (ms) |
| `--visible` | false | Show browser window |
