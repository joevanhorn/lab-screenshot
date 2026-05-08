# Lab Screenshot Bot — Solution Overview

## What Is This Tool?

Lab Screenshot Bot is an AI-powered browser automation tool that reads Okta lab guides and automatically captures screenshots at each step. Instead of a human manually walking through every lab guide, clicking through screens, and taking screenshots, the bot does it autonomously — reading the guide, navigating the Okta Admin Console, executing the lab steps, and embedding real screenshots into the final document.

The bot uses Claude (Anthropic's AI) to understand the guide instructions, reason about what it sees on screen, and decide what to click, fill, or navigate to next. It behaves like a careful human tester: it reads the instructions, looks at the page, takes an action, checks if it worked, and moves on.

### Key Capabilities

- **Guide comprehension**: Reads a markdown lab guide and breaks it into logical sections with goals, steps, and success conditions
- **Browser automation**: Controls a real Chromium browser via Playwright — clicks buttons, fills forms, navigates menus, switches tabs
- **Visual reasoning**: Sees screenshots of each page and uses them to verify actions worked and decide what to do next
- **Okta Admin Console navigation**: Has built-in knowledge of Okta's UI patterns — sidebar navigation, authentication policies, selectize dropdowns, SimpleModal confirmations
- **API operations**: Can make Okta API calls (with an API token) for operations that can't be done through the UI, like enrolling MFA factors for users
- **Human-in-the-loop**: Asks for human help when needed (e.g., approving an MFA push notification) via a chat panel with desktop notifications
- **Screenshot selection**: After navigating the guide, uses AI vision to select the best screenshot for each `[SCREENSHOT]` marker from a gallery of all captured frames

---

## What Can It Be Used For?

### Primary Use Case
Automating screenshot capture for **Okta lab guides** used in Tech{Camp} training and customer demos. Any markdown guide with `[SCREENSHOT: description]` markers can be processed.

### Example Workflow
1. A lab guide author writes a markdown document with instructions and screenshot placeholders
2. The bot follows the guide on a live Okta environment, capturing real screenshots
3. The output is a completed guide with actual screenshots embedded — ready for distribution

### Types of Labs It Can Handle
- **Authentication policy configuration** — Navigating Security > Authentication Policies, editing rules, changing MFA settings
- **Attack simulations** — Running brute force attack simulators, observing results
- **System log investigation** — Navigating to Reports > System Log, reviewing login events
- **User management** — Viewing user profiles, checking factor enrollment status
- **Multi-tab workflows** — Labs that require working across the lab portal, admin console, and virtual desktops simultaneously
- **API-backed operations** — Factor enrollment, user attribute changes, group assignments via Okta API

### What It Produces
- A markdown file with `[SCREENSHOT: ...]` markers replaced by base64-encoded PNG images
- Individual PNG files for each screenshot (optional)
- A web preview of the completed guide at `/preview`

---

## What Might Cause It to Struggle?

### Common Challenges

**Unfamiliar UI patterns**: The bot has specific knowledge of the Okta Admin Console. If a lab involves a third-party application with non-standard UI controls, the bot may struggle to find the right elements to interact with.

**Complex multi-step forms**: Very long forms with many dropdowns and custom controls (like Okta's Selectize dropdowns) can take many iterations. The bot may need 20-30 attempts to complete a complex policy configuration.

**Timing-sensitive operations**: Some operations (like attack simulations) take time to complete. The bot waits and checks periodically, but may run out of iterations if an operation takes longer than expected.

**Virtual desktop interaction**: The bot can click inside embedded virtual desktop viewers (like Heropa), but it's interacting with a canvas/image — it can't read text or identify specific elements inside the VM.

**MFA prompts**: When Okta requires admin MFA for security-sensitive changes, the bot needs a human to approve the push notification. If the human doesn't respond within the timeout (5 minutes), the bot moves on.

**Session expiration**: Long-running guides may hit session timeouts on the Okta org. The bot can't re-authenticate mid-session.

**Custom dropdown controls**: Enterprise web apps often replace native `<select>` elements with custom JavaScript controls (Selectize, Select2, MUI). The bot has strategies for these, but unfamiliar custom controls may require new patterns.

### When to Escalate

If the bot consistently fails on a specific guide or UI pattern:

1. **Check the progress log** — The bot logs its reasoning at each step. Look for repeated failed actions or "max iterations reached" messages.
2. **Check the captured frames** — The recording directory contains PNG screenshots of every action. Review these to see what the bot was looking at when it got stuck.
3. **File an issue** — Report the problem with the guide name, the section that failed, and relevant log output.

### Reporting Issues

**GitHub Issues**: [github.com/joevanhorn/lab-screenshot/issues](https://github.com/joevanhorn/lab-screenshot/issues)

When filing an issue, include:
- The lab guide markdown file (or a link to it)
- The terminal/log output showing where the bot got stuck
- The Okta org URL (if not sensitive)
- Which section failed and what the bot was trying to do
- Screenshots of the page state if available (check the recording directory)

**Labels to use**:
- `bug` — The bot did something wrong
- `ui-pattern` — A new UI control or pattern the bot doesn't handle
- `guide-issue` — The guide itself has unclear instructions that confuse the bot
- `enhancement` — Feature request or improvement idea

---

## How Does It Work?

### Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     Web UI (FastAPI)                      │
│  Upload guide → Configure → Start → Monitor → Download   │
│                    ↕ WebSocket                            │
├─────────────────────────────────────────────────────────┤
│                   Recording Pipeline                      │
│                                                           │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐ │
│  │    Guide     │    │   Pass 1:    │    │   Pass 2:   │ │
│  │ Comprehension│───▶│   Record     │───▶│   Select    │ │
│  │  (LLM Plan) │    │ (Navigation) │    │  (Vision)   │ │
│  └─────────────┘    └──────────────┘    └─────────────┘ │
│         │                  │                    │         │
│         ▼                  ▼                    ▼         │
│    Section Plan      Frame Gallery         Best Frames   │
│  (goals, steps,    (PNG after every      (1 per marker)  │
│   success cond.)      action)                            │
├─────────────────────────────────────────────────────────┤
│                    Browser Control                        │
│  Playwright (Chromium) — click, fill, scroll, navigate   │
│  ↕ Screenshots ↕ DOM queries ↕ JavaScript evaluation     │
├─────────────────────────────────────────────────────────┤
│                    External Services                      │
│  Claude AI (Bedrock/LiteLLM) │ Okta API (SSWS token)    │
└─────────────────────────────────────────────────────────┘
```

### Phase 1: Guide Comprehension

The LLM reads the entire guide and produces a structured execution plan:

```json
{
  "sections": [
    {
      "title": "Execute Brute Force Attack",
      "goal": "Run the attack simulator and observe a successful compromise",
      "context": "Lab guide page — the Tech{Camp} simulator panel",
      "steps": ["Click Execute", "Confirm dialog", "Wait for results", "Observe success"],
      "success_looks_like": "Dialog shows 1/1 successful attempts",
      "screenshot_markers": [0, 1],
      "api_workaround": null
    }
  ]
}
```

This plan determines:
- **What** to do in each section
- **Where** it happens (which tab/page)
- **When** it's done (observable success condition)
- **Whether** an API workaround is needed (for operations requiring mobile devices)

### Phase 2: Section-by-Section Execution

Each section runs independently with:
- A focused system prompt containing the goal, steps, and success condition
- A fresh conversation (no context pollution from previous sections)
- Visual feedback — a screenshot sent to the LLM after every action
- Cumulative progress tracking — the bot sees what it has already done
- Chain-of-thought reasoning: "Where am I? What step am I on? What should I do?"

### Browser Tools

The bot has access to these tools during execution:

| Tool | Purpose |
|------|---------|
| `click` | Click elements (with dialog-scoping and scroll-into-view) |
| `fill` | Fill text inputs |
| `select_option` | Change native `<select>` dropdowns |
| `scroll` | Scroll page or containers (auto-detects dialogs, content areas) |
| `navigate` | Go to a URL directly |
| `get_page_state` | List interactive elements with selectors and row context |
| `get_page_text` | Read visible text content |
| `inspect_element` | DevTools-like DOM inspection for debugging click failures |
| `list_tabs` / `switch_tab` | Multi-tab navigation |
| `wait_for_new_tab` | Wait for and switch to a newly opened tab |
| `wait` | Wait for time or element appearance |
| `browser_api` | Make Okta API calls (SSWS token or session cookies) |
| `ask_human` | Request human input via chat panel |
| `section_complete` | Signal section goal achieved |

### Phase 3: Frame Selection

After all sections execute, the vision model reviews the captured frame gallery:
- Each `[SCREENSHOT]` marker is matched to the best frame
- The LLM sees all frames (up to 18, sampled evenly) and picks the one that best matches the marker description
- Selected frames are embedded as base64 PNG images in the output markdown

### Key Design Patterns

**Reasoning over rules**: The bot thinks step-by-step instead of following rigid rules. This lets it adapt to unexpected UI states, recover from errors, and handle guides it's never seen before.

**Dialog awareness**: All click operations check for open dialogs/modals and scope interactions to the topmost layer. Supports Okta's SimpleModal, MUI dialogs, and native HTML dialogs.

**Okta UI cheat sheet**: The system prompt includes specific guidance for Okta Admin Console patterns — sidebar navigation, selectize dropdowns, policy rule actions, and direct URL fallbacks.

**API workarounds**: When a section requires physical device interaction (mobile MFA enrollment, QR code scanning), the comprehension phase identifies this and generates an API-based workaround that achieves the same outcome programmatically.

**Human-in-the-loop**: The bot detects when it needs human intervention (MFA push approval, ambiguous instructions) and requests help via the chat panel, with desktop notifications and audio alerts. Reminder pings are sent at 30 and 60 seconds if no response is received.

**Tab awareness**: The bot tracks the number of open browser tabs throughout each section. When a new tab opens (e.g., an MFA step-up challenge, an authentication redirect, or a link click), the bot detects it immediately and investigates to determine if the new tab is relevant to the current task. This is especially important for Okta's admin MFA flow, which can open a step-up authentication challenge in a new tab.

**Stuck detection**: Two layers of stuck detection prevent the bot from burning iterations on failed approaches:
1. *Repetition detection*: When the last 3 progress entries target the same element, the bot gets a forced escalation to `ask_human`, tab checking, or a completely different approach.
2. *Budget warning*: At 60% of the iteration budget, the bot receives suggestions to check tabs, use `inspect_element`, or ask for help.

**Closed tab recovery**: Some actions (like MFA approval) cause tabs to close automatically. The bot checks page validity at the start of every iteration and after every tool call. If the current page is closed, it automatically switches to the best remaining tab (preferring the admin console) and logs the recovery.

**Irrelevant dialog filtering**: The bot is instructed to ignore cookie consent banners, promotional popups, and other dialogs unrelated to the current task. Common cookie dialogs (OneTrust, generic accept buttons) are auto-dismissed at the start of each section.

**Cumulative progress tracking**: Each section maintains a log of actions taken and results observed. This log is included in every screenshot prompt so the bot knows what it has already done and doesn't repeat completed actions.

### Technology Stack

| Component | Technology |
|-----------|-----------|
| AI Model | Claude Sonnet 4.6 / Opus 4.6 (via AWS Bedrock or LiteLLM proxy) |
| Browser Automation | Playwright (Chromium) |
| Web UI | FastAPI + vanilla HTML/JS |
| Real-time Updates | WebSocket |
| MFA Code Generation | pyotp (TOTP) |
| API Authentication | Okta SSWS tokens (server-side urllib) |
| Guide Format | Markdown with `[SCREENSHOT: description]` markers |

### File Structure

```
lab-screenshot/
├── lab_screenshot/
│   ├── app.py              # FastAPI web UI + pipeline orchestration
│   ├── cli.py              # Command-line interface (check, login, capture, run, app)
│   ├── recorder.py         # Core: guide comprehension + section execution + tools
│   ├── browser_agent.py    # Alternative LLM agent for manual run --agent mode
│   ├── frame_selector.py   # Pass 2: vision-based frame selection
│   ├── guide.py            # Markdown parser for [SCREENSHOT] markers
│   └── screenshot.py       # Browser profile management + auth
├── tests/
│   ├── mock_lab_server.py      # Mock lab environment for testing
│   ├── mock_okta_server.py     # Mock Okta Admin Console for testing
│   ├── brute-force-guide.md    # Real lab guide (brute force + MFA)
│   └── okta-policy-guide.md    # Focused test guide for policy editing
├── packaging/
│   ├── build-macos-pkg.sh      # macOS .pkg installer builder
│   └── README.md               # Packaging and distribution instructions
├── docs/
│   ├── SOLUTION-OVERVIEW.md    # This document
│   └── LESSONS-LEARNED.md      # Development lessons for future reference
├── .github/ISSUE_TEMPLATE/     # Bug report and UI pattern issue templates
├── install.sh                  # One-line installer script
└── pyproject.toml              # Package configuration
```
