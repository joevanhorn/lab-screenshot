# Lab Screenshot Bot

AI-powered browser automation that reads Okta lab guides, follows the steps, and captures screenshots automatically. Give it a markdown guide with `[SCREENSHOT: description]` markers, and it produces the same guide with real screenshots embedded.

## Install

### One-line install (macOS / Linux)

```bash
curl -sSL https://raw.githubusercontent.com/joevanhorn/lab-screenshot/main/install.sh | bash
```

This will:
- Clone the repo to `~/lab-screenshot`
- Create a Python virtual environment
- Install all dependencies
- Download the Playwright Chromium browser

### Manual install

```bash
git clone https://github.com/joevanhorn/lab-screenshot.git
cd lab-screenshot
python3 -m venv venv
source venv/bin/activate
pip install -e .
playwright install chromium
```

### Requirements
- Python 3.10+
- git

## Quick Start

```bash
cd ~/lab-screenshot
source venv/bin/activate
lab-screenshot app
```

Open **http://localhost:8384** in your browser, then:

1. **Upload** a markdown guide with `[SCREENSHOT: ...]` markers
2. **Configure** the starting URL, AI model, and optionally an Okta API key
3. **Start Recording** — a browser window opens
4. **Authenticate** — log into the Okta org, open needed tabs, navigate to the starting point
5. **Hand Off to Bot** — the bot takes over and follows the guide
6. **Respond when asked** — approve MFA pushes, answer questions via the web UI chat panel or the terminal
7. **Download** the completed guide with embedded screenshots

## Guide Format

Write your lab guide in markdown with `[SCREENSHOT: description]` markers where you want screenshots:

```markdown
## Step 1: Configure the Policy

1. Navigate to **Security > Authentication Policies**
2. Click on **TaskVantage - Apps**
3. Click **Actions > Edit** next to the Employee Access rule
4. Change "User must authenticate with" to **Password + Another factor**
5. Click **Save**

[SCREENSHOT: Authentication policy rule configured with MFA enabled]

## Step 2: Verify

1. Run the attack simulator again
2. Observe that the attack fails

[SCREENSHOT: Attack results showing 0 successful attempts]
```

## Features

- **AI-powered navigation** — Claude reads the guide, comprehends the steps, and executes them like a human tester
- **Visual reasoning** — sees screenshots after every action to verify results and decide next steps
- **Okta Admin Console expertise** — built-in knowledge of sidebar navigation, custom dropdowns, policy editing, and common URL paths
- **API operations** — enrolls MFA factors, manages users, and performs other admin operations via Okta API when browser-only isn't possible
- **Human-in-the-loop** — asks for help when stuck or when admin MFA approval is needed, with desktop notifications and audio alerts. Responds via the web UI chat panel or terminal.
- **Self-debugging** — uses DOM inspection to diagnose click failures and adapt selectors
- **Multi-tab awareness** — detects new tabs (MFA challenges, redirects) and switches between them automatically
- **Stuck detection** — detects when it's repeating the same failed action and escalates: tries different approaches, checks other tabs, or asks the human for guidance
- **Closed tab recovery** — if a tab closes unexpectedly (e.g., after MFA approval), automatically switches to a valid tab and continues

## Configuration

| Setting | Description |
|---------|-------------|
| **Starting URL** | Where the lab begins (e.g., `https://labs.demo.okta.com/lab/your-lab-id`) |
| **AI Model** | Claude Sonnet 4.6 recommended. Opus 4.6 available for complex guides. |
| **Okta API Key** | Optional SSWS token for the target org. Enables factor enrollment and other admin API operations. Generate in Okta Admin Console under Security > API > Tokens. |
| **Use system Chrome** | Check if corporate endpoint security blocks Playwright's Chromium. |

## After a Run

| Button | Description |
|--------|-------------|
| **Download Output** | Completed markdown with base64 screenshots embedded |
| **Preview in Browser** | Rendered HTML view at `/preview` |
| **Download Recording** | Zip of all frame PNGs captured during navigation |
| **Export Debug Bundle** | Zip of input guide + output + console log for bug reports |

## Troubleshooting

- **Bot can't find an element** — it tries multiple selectors, then uses `inspect_element` to debug, then asks for help
- **Screenshots look wrong** — don't minimize or cover the browser window during the run
- **API calls failing** — verify the Okta API key has Super Admin permissions
- **MFA prompt not appearing** — ensure Okta Verify is installed with push notifications enabled

**Report issues**: [github.com/joevanhorn/lab-screenshot/issues](https://github.com/joevanhorn/lab-screenshot/issues)

Use the "Export Debug Bundle" button to attach the input guide, output, and logs to your issue.

## CLI Commands

The web UI (`lab-screenshot app`) is the recommended way to use the tool. CLI commands are also available:

```bash
# Web UI (recommended)
lab-screenshot app

# Check markers in a guide
lab-screenshot check guide.md

# Record with AI agent (opens browser for manual auth, then bot takes over)
lab-screenshot run guide.md --org https://labs.demo.okta.com/... --agent

# Headless login and save browser profile for later use
lab-screenshot login --org https://your-org.okta.com --username bot@org.com --totp-secret SECRET

# Single screenshot capture
lab-screenshot capture --org https://your-org.okta.com --path /admin/dashboard -o screenshot.png
```

## Documentation

- [Solution Overview](docs/SOLUTION-OVERVIEW.md) — architecture, design patterns, how it works
- [Lessons Learned](docs/LESSONS-LEARNED.md) — technical challenges, what worked, recommendations

## License

Internal tool — not for external distribution.
