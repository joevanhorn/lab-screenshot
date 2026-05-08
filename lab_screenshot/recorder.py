#!/usr/bin/env python3
"""
recorder.py — Pass 1: Execute guide steps and record everything.

The agent drives through the guide and captures a screenshot + metadata
after every significant action. Produces a "gallery" of timestamped
frames that Pass 2 uses to select the best match for each marker.

Also records video of the entire session via Playwright's built-in
video recording.
"""

import base64
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Frame:
    """A single captured frame from the recording session."""
    index: int
    timestamp: float          # seconds since session start
    url: str
    title: str
    action: str               # what action preceded this frame
    png_path: Optional[str] = None
    base64_uri: Optional[str] = None


@dataclass
class Recording:
    """Complete recording of a guide execution session."""
    guide_path: str
    admin_url: str
    started_at: str
    frames: list[Frame] = field(default_factory=list)
    video_path: Optional[str] = None


class GuideRecorder:
    """
    Pass 1: Execute guide steps and record a gallery of screenshots.

    Uses the LLM agent to drive the browser, but captures a frame
    after EVERY tool call (not just at markers). The LLM's job in
    Pass 1 is purely navigation — no screenshot selection needed.
    """

    def __init__(
        self,
        page,
        context,  # Playwright BrowserContext — needed for multi-tab support
        admin_url: str,
        output_dir: str = "/tmp/lab-screenshot-recording",
        verbose: bool = True,
        human_input_callback=None,  # Optional: callable(question: str) -> str
        okta_api_key: str = "",  # Optional: SSWS token for direct API calls
    ):
        self.page = page
        self.context = context
        self._human_input_callback = human_input_callback
        self._okta_api_key = okta_api_key.strip() if okta_api_key else ""
        self.admin_url = admin_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.recording = Recording(
            guide_path="",
            admin_url=admin_url,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._start_time = time.time()

    def _log(self, msg: str):
        if self.verbose:
            print(f"  [recorder] {msg}", file=sys.stderr)

    def capture_frame(self, action: str) -> Frame:
        """Capture current page state as a frame."""
        idx = len(self.recording.frames)
        elapsed = time.time() - self._start_time

        # Take screenshot
        png_path = str(self.output_dir / f"frame-{idx:03d}.png")
        self.page.wait_for_timeout(300)
        png_bytes = self.page.screenshot(type="png")
        Path(png_path).write_bytes(png_bytes)

        # Base64 for LLM vision
        b64 = base64.b64encode(png_bytes).decode("ascii")
        b64_uri = f"data:image/png;base64,{b64}"

        frame = Frame(
            index=idx,
            timestamp=round(elapsed, 1),
            url=self.page.url,
            title=self.page.title(),
            action=action,
            png_path=png_path,
            base64_uri=b64_uri,
        )
        self.recording.frames.append(frame)
        self._log(f"frame {idx}: {action} → {frame.url[:60]} ({len(png_bytes):,}b)")
        return frame

    def _extract_steps(self, guide_text: str) -> str:
        """Extract actionable steps from guide, stripping narrative paragraphs.

        Keeps: headers, numbered steps, screenshot markers, tables, NOTE blocks,
        and lines with bold navigation instructions.
        """
        import re
        lines = guide_text.split('\n')
        output = []
        prev_was_blank = False

        for line in lines:
            stripped = line.strip()

            # Always include headers
            if stripped.startswith('#'):
                output.append(line)
                prev_was_blank = False
                continue

            # Include numbered steps (1., 2., etc.)
            if re.match(r'^\d+\.', stripped):
                output.append(line)
                prev_was_blank = False
                continue

            # Include SCREENSHOT markers
            if '[SCREENSHOT:' in stripped:
                output.append(line)
                prev_was_blank = False
                continue

            # Include table rows
            if stripped.startswith('|'):
                output.append(line)
                prev_was_blank = False
                continue

            # Include NOTE blocks
            if stripped.startswith('**NOTE'):
                output.append(line)
                prev_was_blank = False
                continue

            # Include lines with bold navigation/action keywords
            if '**' in stripped and any(kw in stripped.lower() for kw in [
                'from the', 'go to', 'navigate', 'select', 'admin console',
                'click', 'open', 'launch', 'log in', 'sign in',
            ]):
                output.append(line)
                prev_was_blank = False
                continue

            # Keep single blank lines between content (collapse multiples)
            if not stripped:
                if not prev_was_blank and output:
                    output.append('')
                    prev_was_blank = True
                continue

            # Skip narrative paragraphs
            prev_was_blank = False

        return '\n'.join(output)

    def record_guide(self, guide_text: str, max_iterations: int | None = None, max_per_section: int = 25) -> Recording:
        """
        Execute guide steps via LLM and capture frames throughout.
        Returns the complete Recording with all frames.

        max_per_section caps each section's iteration budget. The dynamic floor
        (15 iters or 4×steps, whichever is larger) still applies — this is the
        ceiling. Default 25 matches prior hardcoded behavior. Surfaced in the
        web UI for users who want to give complex sections more headroom.

        max_iterations is the overall budget across all sections. When None
        (the typical case), it's auto-computed as num_sections × max_per_section.
        """
        from .guide import parse_markers

        # Clean guide text (strip base64 images)
        import re
        clean_text = re.sub(r'\[image\d+\]:\s*<data:image[^>]*>', '', guide_text)
        clean_text = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[existing-image]', clean_text)
        lines = clean_text.split('\n')
        clean_lines = [l if len(l) < 500 else l[:100] + '...[truncated]' for l in lines]
        clean_text = '\n'.join(clean_lines)

        self._log(f"Guide: {len(guide_text):,} → {len(clean_text):,} chars (cleaned)")

        # Extract condensed action steps for the LLM
        action_steps = self._extract_steps(clean_text)
        self._log(f"Extracted action steps: {len(clean_text):,} → {len(action_steps):,} chars")

        markers = parse_markers(guide_text)
        self._log(f"Found {len(markers)} screenshot markers")

        # Capture initial frame
        self.capture_frame("session_start")

        # Use LLM to drive navigation
        try:
            self._drive_with_llm(clean_text, action_steps, markers, max_iterations, max_per_section)
        except Exception as e:
            self._log(f"LLM navigation error: {e}")

        # Save recording metadata
        meta_path = self.output_dir / "recording.json"
        meta = {
            "guide_path": self.recording.guide_path,
            "admin_url": self.recording.admin_url,
            "started_at": self.recording.started_at,
            "total_frames": len(self.recording.frames),
            "frames": [
                {
                    "index": f.index,
                    "timestamp": f.timestamp,
                    "url": f.url,
                    "title": f.title,
                    "action": f.action,
                    "png_path": f.png_path,
                }
                for f in self.recording.frames
            ],
        }
        meta_path.write_text(json.dumps(meta, indent=2))
        self._log(f"Recording saved: {len(self.recording.frames)} frames → {self.output_dir}")

        return self.recording

    def _capture_page_b64(self) -> str:
        """Capture current page as base64 PNG for LLM vision."""
        png_bytes = self.page.screenshot(type="png")
        return base64.b64encode(png_bytes).decode("ascii")

    # Dialog/modal CSS selector — priority order: Okta SimpleModal (topmost) > MUI > native
    DIALOG_CSS = '#simplemodal-container, [role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]'

    # -- Browser tool definitions (shared across all execution phases) --
    TOOLS = [
        {"name": "navigate", "description": "Navigate to a full URL. Only use when the guide gives you an explicit URL — prefer clicking links/buttons.", "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "click", "description": "Click an element. Use text selectors: 'text=Security', 'button:has-text(\"Save\")', 'a:has-text(\"Reports\")', '[data-se=\"save\"]'. For menus, click the header first, wait, then sub-items. When multiple elements match, use nth= to pick the right one: 'button:has-text(\"Actions\") >> nth=0'.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["selector"]}},
        {"name": "fill", "description": "Fill an input. Use get_page_state first to find name/id, then: input[name=\"x\"], input#id.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "value": {"type": "string"}}, "required": ["selector", "value"]}},
        {"name": "select_option", "description": "Select an option from a native <select> dropdown. Use this instead of click for <select> elements. Provide the selector for the <select> and the option value or label.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector for the <select> element"}, "value": {"type": "string", "description": "The option value or visible label to select"}}, "required": ["selector", "value"]}},
        {"name": "scroll", "description": "Scroll the page or a specific element. Use direction 'down' or 'up'. Use this when you need to see content below/above the current viewport, or when an element is not visible.", "input_schema": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction"}, "pixels": {"type": "integer", "description": "Pixels to scroll. Default 400."}, "selector": {"type": "string", "description": "Optional CSS selector of the element to scroll. Omit for page scroll."}}, "required": ["direction"]}},
        {"name": "get_page_state", "description": "Get current URL, title, and visible interactive elements with their selectors and row/parent context. If a dialog/modal is open, shows only dialog elements.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_page_text", "description": "Get visible text of current page or a CSS-scoped section.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}}},
        {"name": "wait", "description": "Wait milliseconds or for a CSS selector to appear.", "input_schema": {"type": "object", "properties": {"milliseconds": {"type": "integer"}, "selector": {"type": "string"}}}},
        {"name": "list_tabs", "description": "List all open browser tabs.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "switch_tab", "description": "Switch to tab by index. Use list_tabs first.", "input_schema": {"type": "object", "properties": {"tab_index": {"type": "integer"}}, "required": ["tab_index"]}},
        {"name": "wait_for_new_tab", "description": "Wait for a new tab to open after clicking a link/button.", "input_schema": {"type": "object", "properties": {"timeout": {"type": "integer"}}}},
        {"name": "section_complete", "description": "Signal that the current section's goal has been achieved. Include a brief reason.", "input_schema": {"type": "object", "properties": {"reason": {"type": "string", "description": "Brief explanation of why this section is complete"}}}},
        {"name": "ask_human", "description": "Ask the human operator for help when you are genuinely stuck or uncertain. Use this when: you've tried 2-3 approaches and none worked, you're unsure which element to interact with, or you need clarification about what the guide means. Do NOT use this as a first resort — try to solve the problem yourself first.", "input_schema": {"type": "object", "properties": {"question": {"type": "string", "description": "Clear question for the human. Describe what you see, what you tried, and what you need help with."}}, "required": ["question"]}},
        {"name": "browser_api", "description": "Make an Okta API call using the browser's authenticated admin session. Use this to perform operations that can't be done through the UI (e.g., enrolling a factor for a user when mobile device access is unavailable). The browser's session cookies authenticate the request automatically. Do NOT use this for operations the admin needs to approve via MFA — those must be done through the UI so the human can complete the MFA challenge.", "input_schema": {"type": "object", "properties": {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "description": "HTTP method"}, "path": {"type": "string", "description": "API path starting with /api/v1/... (e.g., /api/v1/users?search=profile.email eq \"user@example.com\")"}, "body": {"type": "object", "description": "JSON body for POST/PUT requests"}}, "required": ["method", "path"]}},
        {"name": "inspect_element", "description": "Inspect a DOM element like browser DevTools. Use this when a click isn't working as expected — it reveals the element's actual tag, classes, attributes, whether it's obscured by another element, and suggests better selectors. Helps debug why clicks fail.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string", "description": "Playwright selector for the element to inspect"}}, "required": ["selector"]}},
    ]

    def _drive_with_llm(self, guide_text: str, action_steps: str, markers, max_iterations: int | None, max_per_section: int = 25):
        """Comprehend guide, then execute section by section."""
        try:
            from litellm import completion
        except ImportError:
            self._log("litellm not available — capturing initial frame only")
            return

        self._completion = completion
        self._model_id = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
        self._litellm_extra = {}
        if os.environ.get("LITELLM_API_BASE"):
            self._litellm_extra["api_base"] = os.environ["LITELLM_API_BASE"]
            self._litellm_extra["api_key"] = os.environ.get("LITELLM_API_KEY", "")

        # Phase 1: Comprehend the guide
        sections = self._comprehend_guide(guide_text, action_steps, markers)

        # Auto-derive overall budget from per-section cap when caller didn't set it.
        if max_iterations is None:
            max_iterations = max(50, len(sections) * max_per_section)

        # Phase 2: Execute each section
        total_iters_used = 0
        # Budget: scale per section by step count, minimum 15, cap at max_per_section (user-configurable)
        for s in sections:
            if not s.get("skip_reason") or s.get("api_workaround"):
                n_steps = len(s.get("steps", []))
                s["_budget"] = min(max_per_section, max(15, n_steps * 4))

        for i, section in enumerate(sections):
            if section.get("skip_reason") and not section.get("api_workaround"):
                self._log(f"SKIP section {i+1}/{len(sections)}: {section['title']} — {section['skip_reason']}")
                continue

            if section.get("api_workaround"):
                # Section can't be done via UI but has an API workaround
                self._log(f"=== Section {i+1}/{len(sections)}: {section['title']} (API workaround) ===")
                self._log(f"  Workaround: {section['api_workaround']}")
                budget = min(20, section.get("_budget", 15))
                iters_used = self._execute_section(section, max_iterations=budget)
            else:
                budget = section.get("_budget", 20)
                self._log(f"=== Section {i+1}/{len(sections)}: {section['title']} ({budget} iters) ===")
                iters_used = self._execute_section(section, max_iterations=budget)
            total_iters_used += iters_used

            if total_iters_used >= max_iterations:
                self._log("Max total iterations reached")
                break

        self._log(f"Navigation complete: {len(self.recording.frames)} frames captured in {total_iters_used} iterations")

    def _comprehend_guide(self, guide_text: str, action_steps: str, markers) -> list[dict]:
        """Phase 1: LLM reads the guide and produces a structured execution plan."""
        marker_list = "\n".join(f"  [{m.index}] {m.description}" for m in markers)

        prompt = f"""Read this lab guide carefully and break it into executable sections for a browser automation agent.

You are planning for an agent that controls a browser with multiple tabs open. The agent can see screenshots, click elements, fill forms, and switch between tabs. It CANNOT interact with content inside embedded remote desktops or virtual machines (those are just images in the browser).

For each section, identify:
1. **title**: Short descriptive name
2. **goal**: What should be accomplished (1-2 sentences)
3. **context**: WHERE does this section take place? Read the guide instructions to determine this. Examples:
   - "Lab guide page — the simulator/tool panel on the right side of the guide"
   - "Admin Console — navigate via sidebar menu"
   - "Same page as previous section"
   Think about which tab or page the instructions refer to. If the guide says "From the Admin Console, go to..." then context is the Admin Console tab. If the guide says "From Tech{{Camp}} - Attack Simulator..." then context is the lab guide page where the simulator panel lives.
4. **steps**: Specific browser actions needed (click X, navigate to Y, fill in Z, observe W)
5. **success_looks_like**: How to know this section is DONE — what should be visible on screen
6. **screenshot_markers**: Which marker indices should be captured during this section (can be empty)
7. **skip_reason**: Set ONLY if the section truly cannot be accomplished at all. Set to null in most cases.
8. **api_workaround**: If a section requires a mobile device or virtual desktop interaction that can't be done in the browser, but the same OUTCOME could be achieved via an Okta API call, describe the API approach here. The agent has a browser_api tool that can make authenticated API calls using the admin session. Common examples:
   - Enrolling a factor for a user: "Use browser_api to POST /api/v1/users/{{userId}}/factors to enroll a TOTP factor, then activate it"
   - Assigning an app to a user: "Use browser_api to PUT /api/v1/apps/{{appId}}/users/{{userId}}"
   Set to null if no workaround is needed (the section can be done via browser UI).
   NOTE: Do NOT use api_workaround for operations that require admin MFA approval (like saving security policy changes) — those MUST be done through the UI so the human admin can complete the MFA challenge.

## Screenshot markers in the guide:
{marker_list}

## Rules:
- Group related steps by guide heading/section
- Each section needs a CLEAR, OBSERVABLE completion condition
- **Read the guide to determine context.** Look for phrases that indicate WHERE the action happens. Examples: "From the Admin Console, go to..." means the Admin Console tab. "From the lab guide, click..." means the lab guide page. If a tool or simulator panel is referenced on the lab page (e.g., an attack simulator, a configuration tool), that section happens on the lab guide page, not a separate tab.
- **Prefer API workarounds over skipping.** If a section requires a mobile device (e.g., enroll Okta Verify, scan QR code) or a virtual desktop, think about whether the same outcome can be achieved via an Okta API call. For example, factor enrollment can be done via `/api/v1/users/{{userId}}/factors`. Only set skip_reason if there is truly no workaround.
- **Never use api_workaround for admin MFA.** If the guide says "Save and provide MFA if required", that must happen through the UI — the human admin will complete the MFA push.
- If a section triggers an async operation (simulation, API call): click the button, confirm any dialog, wait for results, observe
- For success_looks_like, be PRAGMATIC — don't require specific result text you can't know in advance
- Assign each screenshot marker to exactly one section
- When the same tool/panel appears in multiple sections (e.g., running the same simulator twice), make sure the context is correct for each occurrence

## Condensed action steps:
{action_steps}

## Full guide for context:
{guide_text}

Respond with ONLY a JSON object:
{{"sections": [{{"title": "...", "goal": "...", "context": "...", "steps": ["..."], "success_looks_like": "...", "screenshot_markers": [0], "skip_reason": null, "api_workaround": null}}, ...]}}"""

        try:
            response = self._completion(
                model=self._model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                **self._litellm_extra,
            )
            reply = response.choices[0].message.content

            # Parse JSON (handle markdown code blocks)
            if "```" in reply:
                reply = reply.split("```")[1]
                if reply.startswith("json"):
                    reply = reply[4:]

            plan = json.loads(reply.strip())
            sections = plan.get("sections", [])

            self._log(f"Comprehended guide → {len(sections)} sections:")
            for s in sections:
                skip = f" [SKIP: {s.get('skip_reason')}]" if s.get('skip_reason') and not s.get('api_workaround') else ""
                api = " [API WORKAROUND]" if s.get('api_workaround') else ""
                markers_str = s.get('screenshot_markers', [])
                ctx = s.get('context', '')
                self._log(f"  • {s['title']}{skip}{api} (markers: {markers_str}) — {ctx}")

            return sections

        except Exception as e:
            self._log(f"Guide comprehension failed: {e} — falling back to single section")
            return [{
                "title": "Execute all guide steps",
                "goal": "Follow all steps in the guide in order",
                "steps": ["Follow the guide instructions in order"],
                "success_looks_like": "All pages referenced in the guide have been visited",
                "screenshot_markers": [m.index for m in markers],
                "skip_reason": None,
            }]

    def _execute_section(self, section: dict, max_iterations: int = 25) -> int:
        """Execute one section of the guide. Returns iterations used."""

        title = section["title"]
        goal = section["goal"]
        context = section.get("context", "")
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(section["steps"]))
        success = section["success_looks_like"]

        api_workaround = section.get("api_workaround")

        context_instruction = ""
        if context:
            context_instruction = f"\n**Where:** {context}\nMake sure you are on the correct tab/page for this section BEFORE executing steps. Use list_tabs and switch_tab if needed.\n"
        if api_workaround:
            context_instruction += f"\n**⚠ API WORKAROUND:** This section normally requires a mobile device or virtual desktop, but you can achieve the same outcome using the browser_api tool. Approach: {api_workaround}\nUse the browser_api tool to make the necessary API calls. You have full admin API access via the browser session.\n"

        system = f"""You are a human tester working through a lab guide in a real browser. You think carefully about each step, observe the results of your actions, and adapt when things don't go as expected.

## YOUR CURRENT TASK
**Goal:** {goal}{context_instruction}
**Steps:**
{steps}
**Done when:** {success}

## HOW YOU WORK

Before EVERY action, think step by step (write your reasoning in your response):

1. **Where am I?** Look at the screenshot. What page am I on? What's visible? Is there a dialog, popup, or overlay?
2. **What step am I on?** Which step from the list above am I working on right now?
3. **What should I do?** Based on what I see, what's the right next action?
4. **What do I expect?** After I do this, what should happen? (page changes, dialog appears, content loads, etc.)

After each action, look at the screenshot you receive and assess:
- **Did it work?** Compare what you see to what you expected.
- **Am I done?** Have I completed all the steps? If yes → call section_complete with a brief reason.
- **Did a new tab open?** Some actions (saving policies, clicking links) open new tabs for MFA challenges or authentication prompts. If a new tab opened, use list_tabs to find it and switch_tab to investigate.
- **Am I stuck?** If the same thing keeps happening: (1) use list_tabs to check if a new tab opened that needs attention, (2) use inspect_element to debug a click that isn't working, (3) try a completely different approach, (4) call ask_human if nothing works.

## KEY PRINCIPLES

- **Observe, don't assume.** Look at the screenshot carefully. Read any text, dialogs, messages, or status indicators.
- **Actions have consequences.** After you click something, SOMETHING changed — a dialog opened, a page loaded, content updated, or the action completed silently. Look for the change.
- **Never repeat yourself.** If you clicked a button and the page responded (even by opening a dialog), do NOT click it again. The action worked. Deal with whatever appeared next.
- **Ignore cookie/consent banners.** If you see a cookie dialog or consent banner, ignore it entirely — do NOT waste iterations trying to dismiss it. Just proceed with the task. Use `force: true` on your click if something seems to be blocking it.
- **Dialogs need attention — but only relevant ones.** If a dialog/popup is open AND it's related to the task (confirmation, form, results), interact with it. If it's unrelated (cookies, promotions, surveys), ignore it.
- **Know when to move on.** You don't need to see perfect results. If you completed the steps and the page has responded, that's enough. Call section_complete.
- **Debug before giving up.** If a click isn't working, use inspect_element to see the actual DOM — it may be an `<a>` not a `<button>`, or obscured by an overlay. Use this info to try a better selector or force-click.
- **Ask for help.** If you're genuinely stuck after trying multiple approaches AND inspect_element, call ask_human. Describe what you see and what you've tried.

## TOOL TIPS
- Use click with text selectors: `text=Security`, `button:has-text("Save")`, `a:has-text("Reports")`
- Use get_page_state to discover element selectors (name/id attributes)
- Call list_tabs early to find tabs the human may have opened during setup
- Use wait(3000-5000) after actions that trigger async operations
- When multiple elements match the same text, use `>> nth=0` or `>> nth=1` to pick a specific one

## OKTA ADMIN CONSOLE — UI PATTERNS
If you are working in the Okta Admin Console, these patterns will help:

**Sidebar Navigation (left menu):**
- The sidebar has collapsible sections: Dashboard, Directory, Customizations, Applications, Identity Governance, Security, Workflow, Reports, Settings
- Sections with a `>` chevron EXPAND on click to reveal sub-items. Clicking "Security" doesn't navigate — it expands to show sub-items.
- **IMPORTANT: If a sidebar click doesn't work after 2 attempts, use direct URL navigation instead.** Verified admin URLs:
  **Dashboard & Overview:**
  - Dashboard: `/admin/dashboard`
  - System Log: `/report/system_log_2`
  **Directory:**
  - People (Users): `/admin/users`
  - Groups: `/admin/groups`
  **Applications:**
  - Applications (active): `/admin/apps/active`
  **Security:**
  - Authentication Policies: `/admin/authentication-policies`
  - App Sign-In Policies: `/admin/authentication-policies/app-sign-in`
  - Global Session Policy: `/admin/access/policies`
  - Network Zones: `/admin/access/networks`
  - Identity Providers: `/admin/access/identity-providers`
  - Behavior Detection: `/admin/access/behaviors`
  **Customizations:**
  - Brands: `/admin/customizations/brands`
  **Workflow:**
  - Event Hooks: `/admin/workflow/eventhooks`
  **Settings:**
  - General: `/admin/settings/account`
  - Features: `/admin/settings/features`
  - API Tokens: `/admin/access/api/tokens`
  - Downloads: `/admin/settings/downloads`
  Construct the full URL from the admin domain visible in the address bar (e.g., `https://your-org-admin.okta.com/admin/dashboard`).
- Tip: `get_page_state` will show sidebar items with their `data-se` attributes

**Authentication Policies:**
- The policy list page (`/admin/authentication-policies/app-sign-in`) shows policies in a scrollable content area
- To find a specific policy, use `scroll(down)` on the main content, or try clicking it directly — `get_page_state` may show it even if it's below the visible area
- Each policy opens a rules view with Priority, Rule, Status, and Actions columns

**Policy Rules — Actions Dropdown:**
- There are TWO types of "Actions" buttons — policy-level (top) and row-level (per rule). You want the ROW-level one.
- **To click the right Actions button:** Use `a:has-text("Actions") >> nth=0` for the first rule's Actions. If that opens a policy dropdown (showing Clone, Merge, Edit name), close it by clicking elsewhere and try `a:has-text("Actions") >> nth=1`.
- After the row Actions dropdown opens (showing Edit, Deactivate, Delete), click `text=Edit`
- If you keep hitting the wrong Actions button, try: navigate away and back, then click `a:has-text("Actions") >> nth=0` again

**Edit Rule Dialog:**
- The Edit Rule dialog is a SCROLLABLE modal with IF conditions at the top and THEN settings at the bottom
- Use `scroll(down, 600)` to skip past the IF section and reach the THEN section
- The THEN section contains: Access (Denied/Allowed), authentication requirements, MFA settings
- **Custom dropdowns (Selectize, Chosen, etc.):**
  Okta uses custom dropdown libraries that HIDE the native `<select>` element and replace it with styled divs. Clicking the native `<select>` directly will NOT visually open the dropdown.
  To change a custom dropdown:
  1. **Find the dropdown's fieldset container.** Use `inspect_element` or `get_page_state` to find the wrapping `div[data-se="o-form-fieldset"]` that contains the label text you're looking for.
  2. **Click the container's input area:** `click` with `div[data-se="o-form-fieldset"]:has-text("<label text>") div.o-form-input` — this opens the dropdown regardless of whether it's Selectize, Chosen, or native.
  3. **Click the option text:** `click` with `text=<option you want>` — the dropdown should now be open showing options.
  If you accidentally open the wrong dropdown, click the currently selected value to close it, then try the correct fieldset.
  **DO NOT** spend iterations clicking native `<select>` elements or trying library-specific selectors (`.selectize-input`, `.chzn-container`). The fieldset approach above works universally.
- **CRITICAL: After changing the dropdown, follow this EXACT sequence:**
  1. `scroll(down, 2000)` — jump straight to the bottom
  2. Click Save: try `input[value="Save"]` or `[data-se="save"]`
  3. Okta may show a "Save anyway" confirmation if the policy change reduces security assurance (e.g., weaker MFA requirements). Click it with: `input[value="Save anyway"]` (it's an input, not a button!)
  4. After clicking Save anyway, Okta may require **admin MFA step-up authentication**. If you see text about "protected action" or "step-up authentication" or an authenticator selection screen:
     - FIRST: Look for a "Send push" or "Verify" or "Select" button in the MFA prompt and CLICK IT to trigger the push notification to the admin's phone
     - THEN: Call `ask_human` with: "I clicked Send Push to trigger the MFA notification. Please approve it on your device and let me know when done."
     - Then `wait(15000)` for the approval to complete
     - The dialog should close automatically after MFA approval
  DO NOT scroll through intermediate fields to verify them. DO NOT keep clicking Save — it worked the first time.

**General Scrolling:**
- Okta uses scrollable content areas, NOT page-level scroll for most lists and dialogs
- If `scroll(down)` doesn't change what you see, the content area may need a different scroll target
- For dialogs, scroll targets the dialog automatically
- For main content, the scroll tool auto-detects the Okta content container

## OKTA API REFERENCE (Identity Engine)
All Okta orgs are Identity Engine (OIE). NEVER use Classic-only APIs. When using browser_api:

**Users:**
- Find user: `GET /api/v1/users?search=profile.email eq "user@example.com"`
- Get user: `GET /api/v1/users/{{userId}}`

**Authenticators (OIE replaces classic "Factors"):**
- List org authenticators: `GET /api/v1/authenticators`
- The classic `/api/v1/users/{{userId}}/factors` POST may return 403 on OIE orgs

**Enrolling a TOTP factor for a user:**
Use the browser_api tool with SSWS token (must be provided in app UI):
1. Find the user: `GET /api/v1/users?q={{email}}`
2. Enroll TOTP: `POST /api/v1/users/{{userId}}/factors` with body `{{"factorType": "token:software:totp", "provider": "OKTA"}}`
3. The system will **auto-activate** the factor — it generates a TOTP code using the sharedSecret and calls the activation endpoint automatically. You don't need to do anything extra.
4. The response will confirm "Factor enrolled AND activated successfully!"
5. If enrollment fails with 403, use ask_human to request manual factor enrollment

**Groups:**
- List groups: `GET /api/v1/groups?q={{name}}`
- Add user to group: `PUT /api/v1/groups/{{groupId}}/users/{{userId}}`

**Apps:**
- List apps: `GET /api/v1/apps?q={{name}}`
- Assign user to app: `POST /api/v1/apps/{{appId}}/users` with `{{"id": "{{userId}}"}}`"""

        messages = [{"role": "user", "content": f"Execute this section: **{title}**\n\nGoal: {goal}\n\nSteps:\n{steps}\n\nDone when: {success}"}]

        # Dismiss any cookie/consent dialogs before starting
        try:
            for cookie_sel in [
                '#onetrust-accept-btn-handler',
                'button:has-text("Accept All")',
                'button:has-text("Accept Cookies")',
                'button:has-text("Allow All")',
                '[data-testid="cookie-accept"]',
                '.onetrust-close-btn-handler',
            ]:
                btn = self.page.locator(cookie_sel).first
                if btn.count() > 0 and btn.is_visible(timeout=500):
                    btn.click(timeout=2000)
                    self.page.wait_for_timeout(500)
                    self._log(f"  Dismissed cookie dialog: {cookie_sel}")
                    break
        except Exception:
            pass

        # Cumulative progress log — tracks what the bot has done and observed
        progress_log = []
        initial_tab_count = len(self.context.pages)

        for iteration in range(max_iterations):
            self._log(f"  [{title[:30]}] iteration {iteration + 1}/{max_iterations}")

            # Safety: ensure current page is still valid
            try:
                self.page.url
            except Exception:
                valid_pages = [p for p in self.context.pages if not p.is_closed()]
                if valid_pages:
                    for p in valid_pages:
                        if "-admin." in p.url or "/admin/" in p.url:
                            self.page = p
                            break
                    else:
                        self.page = valid_pages[0]
                    self._log(f"  ⚠ Page was closed, recovered to: {self.page.url[:60]}")
                else:
                    self._log(f"  ⚠ All pages closed, cannot continue")
                    break

            # Build messages with ephemeral screenshot + progress summary
            call_messages = list(messages)
            if iteration > 0:
                try:
                    page_b64 = self._capture_page_b64()
                except Exception as page_err:
                    # Page may have closed (e.g., MFA tab closed after approval)
                    self._log(f"  ⚠ Screenshot failed ({page_err}), recovering...")
                    valid_pages = [p for p in self.context.pages if not p.is_closed()]
                    if valid_pages:
                        for p in valid_pages:
                            if "-admin." in p.url or "/admin/" in p.url:
                                self.page = p
                                break
                        else:
                            self.page = valid_pages[0]
                        self._log(f"  ⚠ Recovered to: {self.page.url[:60]}")
                        self.page.wait_for_timeout(2000)
                        page_b64 = self._capture_page_b64()
                    else:
                        self._log(f"  ⚠ All pages closed, cannot continue")
                        break
                try:
                    # Detect open dialogs and include their text
                    dialog_text = self.page.evaluate(
                        f'() => {{ const d = document.querySelector(\'{self.DIALOG_CSS}\'); return d ? d.innerText.substring(0, 500) : null; }}'
                    )

                    # Detect new tabs that opened since section started
                    current_tab_count = len(self.context.pages)
                    new_tabs_opened = current_tab_count > initial_tab_count

                    # Build progress-aware hint
                    parts = ["Here is the current page."]

                    # Alert about new tabs
                    if new_tabs_opened:
                        parts.append(f'⚠ NEW TAB DETECTED: There are now {current_tab_count} tabs (was {initial_tab_count} when this section started). A new tab may have opened for an MFA challenge, authentication prompt, or related action. Use list_tabs to check what opened, and switch_tab if it is relevant to your current task.')
                    if dialog_text:
                        parts.append(f'⚠ A DIALOG is open. Text: "{dialog_text[:300]}"')
                        # Detect admin MFA step-up authentication
                        dt_lower = dialog_text.lower()
                        save_anyway_clicked = any('Save anyway' in entry for entry in progress_log)
                        if any(kw in dt_lower for kw in ['protected action', 'step-up', 'step up', 'push notification', 'send push']):
                            parts.append('⚠ ADMIN MFA STEP-UP DETECTED! First look for a "Send push" or "Verify" button and click it to trigger the notification. Then IMMEDIATELY call ask_human to tell the admin to approve the push on their phone. Do NOT wait or keep checking — call ask_human NOW.')
                        elif save_anyway_clicked:
                            # We already clicked Save anyway but the dialog is still open — must be MFA
                            # Check if a new tab opened for MFA step-up
                            tab_count = len(self.context.pages)
                            parts.append(f'⚠ You already clicked "Save anyway" but the dialog is still open. This means Okta is waiting for ADMIN MFA step-up authentication. There are {tab_count} tabs open — CHECK if a new tab opened for the MFA challenge (use list_tabs). If so, switch to it and click "Send push" or "Verify". Then call ask_human to request the admin approve the push. Do NOT wait — act NOW.')
                    if progress_log:
                        parts.append("## YOUR PROGRESS SO FAR (do NOT repeat completed actions)")
                        for entry in progress_log[-10:]:
                            parts.append(f"- {entry}")
                        parts.append(f"\nYou have taken {len(progress_log)} actions so far. Do NOT repeat actions you have already completed successfully.")

                    # Repetition detection: if the last 3 actions look similar, the bot is stuck
                    if len(progress_log) >= 3:
                        recent = progress_log[-3:]
                        # Check if all 3 recent actions target the same element/area
                        import re
                        targets = []
                        for entry in recent:
                            # Extract the key target from entries like "Clicked 'text=Security' → ..."
                            match = re.search(r"Clicked '([^']{5,})'|Scrolled|API:|Selected", entry)
                            targets.append(match.group(0)[:30] if match else entry[:30])
                        if len(set(targets)) == 1:
                            parts.append(f"\n🚨 YOU ARE STUCK: Your last 3 actions were all the same ({targets[0]}). This approach is not working. You MUST try something different: (1) call ask_human to describe what you see and ask for guidance, (2) use list_tabs to check other tabs, (3) use inspect_element to understand why the click isn't working, or (4) try a completely different selector or approach.")

                    # Iteration budget warning
                    if iteration > max_iterations * 0.6:
                        parts.append(f"\n⚠ You are on iteration {iteration + 1}/{max_iterations}. If you are stuck, try: (1) list_tabs to check if something opened in another tab, (2) inspect_element to debug a click that isn't working, (3) ask_human for guidance.")

                    parts.append("\nLook at this screenshot and your progress above. If you have completed all the steps in this section, call section_complete NOW. Do not re-do steps that already succeeded.")

                    call_messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "\n".join(parts)},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_b64}"}}
                        ]
                    })
                except Exception as e:
                    self._log(f"  Screenshot failed: {e}")

            try:
                response = self._completion(
                    model=self._model_id,
                    messages=call_messages,
                    tools=self.TOOLS,
                    max_tokens=4096,
                    system=system,
                    **self._litellm_extra,
                )
            except Exception as e:
                self._log(f"  LLM error: {e}")
                break

            message = response.choices[0].message

            # Log any reasoning the LLM wrote, and capture key observations in progress
            if message.content:
                text = message.content if isinstance(message.content, str) else str(message.content)
                for line in text.strip().split('\n')[:5]:
                    self._log(f"  💭 {line}")
                # If the bot's reasoning mentions successful completion, record it
                text_lower = text.lower()
                if any(kw in text_lower for kw in ['completed successfully', 'simulation completed', 'attack completed', '1/1 successful']):
                    progress_log.append(f"✅ OBSERVED: Bot confirmed successful completion in its reasoning")

            messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})

            if not message.tool_calls:
                # LLM responded with text only — might be reasoning before acting, continue
                break

            tool_results = []
            section_done = False

            for tc in message.tool_calls:
                result = self._execute_tool(tc)
                fname = tc.function.name
                if fname == "section_complete":
                    section_done = True

                # Safety: if current page was closed (e.g., MFA tab closed after approval),
                # switch to a valid page
                try:
                    self.page.url  # Test if page is still valid
                except Exception:
                    valid_pages = [p for p in self.context.pages if not p.is_closed()]
                    if valid_pages:
                        # Prefer admin tab, then lab tab
                        for p in valid_pages:
                            if "-admin." in p.url or "/admin/" in p.url:
                                self.page = p
                                break
                        else:
                            self.page = valid_pages[0]
                        self._log(f"  ⚠ Page was closed, switched to: {self.page.url[:60]}")
                        result += f"\n(Note: Previous tab was closed. Now on: {self.page.url[:80]})"

                # Record progress for actions that change page state
                if fname in ("click", "fill", "navigate", "switch_tab", "scroll", "browser_api", "select_option"):
                    try:
                        tc_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tc_args = {}
                    short_result = result[:200]
                    if fname == "click":
                        progress_log.append(f"Clicked '{tc_args.get('selector', '')[:50]}' → {short_result}")
                    elif fname == "fill":
                        progress_log.append(f"Filled '{tc_args.get('selector', '')[:30]}' with '{tc_args.get('value', '')[:20]}'")
                    elif fname == "navigate":
                        progress_log.append(f"Navigated to {tc_args.get('url', '')[:60]}")
                    elif fname == "switch_tab":
                        progress_log.append(f"Switched to tab {tc_args.get('tab_index', '?')} → {short_result}")
                    elif fname == "scroll":
                        progress_log.append(f"Scrolled {tc_args.get('direction', '?')} {tc_args.get('pixels', 400)}px")
                    elif fname == "select_option":
                        progress_log.append(f"Selected '{tc_args.get('value', '')[:30]}' from '{tc_args.get('selector', '')[:30]}'")
                    elif fname == "browser_api":
                        progress_log.append(f"API: {tc_args.get('method', '?')} {tc_args.get('path', '')[:50]} → {short_result}")
                elif fname == "wait":
                    progress_log.append(f"Waited ({result[:60]})")

                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.extend(tool_results)

            if section_done:
                self._log(f"  [{title[:30]}] complete ✓")
                break
        else:
            self._log(f"  [{title[:30]}] max iterations reached")

        return iteration + 1

    def _execute_tool(self, tc) -> str:
        """Execute a single tool call. Returns the result string."""
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            args = {}

        if name == "ask_human":
            question = args.get("question", "The bot needs help.")
            self._log(f"  🙋 ASK HUMAN: {question}")
            # Try to get input from the human
            try:
                if self._human_input_callback:
                    answer = self._human_input_callback(question)
                else:
                    # Fallback: print to stderr and read from stdin
                    print(f"\n🙋 BOT ASKS: {question}", file=sys.stderr)
                    print("   Type your answer (or press Enter to skip): ", file=sys.stderr, end="", flush=True)
                    answer = input().strip()
                    if not answer:
                        answer = "No answer provided. Use your best judgment and continue."
            except (EOFError, OSError):
                answer = "Human input not available. Use your best judgment and continue."
            self._log(f"  👤 HUMAN: {answer}")
            return f"Human response: {answer}"
        elif name == "section_complete":
            reason = args.get("reason", "")
            if reason:
                self._log(f"  ✓ Reason: {reason}")
            return "Section marked complete."
        elif name == "scroll":
            direction = args.get("direction", "down")
            pixels = args.get("pixels", 400)
            selector = args.get("selector")
            delta = pixels if direction == "down" else -pixels
            try:
                if selector:
                    # User specified a specific element to scroll
                    self.page.locator(selector).first.evaluate(f"el => el.scrollBy(0, {delta})")
                    target_desc = f"'{selector}'"
                else:
                    # Auto-detect the right scroll target:
                    # 1. If a dialog/modal is open, scroll the dialog
                    # 2. If Okta admin content area exists, scroll that
                    # 3. Fall back to window scroll
                    target_desc = self.page.evaluate("""(delta) => {
                        // Priority 1: Open dialog (SimpleModal first, then MUI, then native)
                        const dialog = document.querySelector('#simplemodal-container, [role="dialog"]:not([aria-hidden="true"]) .MuiDialogContent-root, [role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root .MuiPaper-root, dialog[open]');
                        if (dialog && dialog.scrollHeight > dialog.clientHeight) {
                            dialog.scrollBy(0, delta);
                            return 'dialog';
                        }
                        // Priority 2: Okta admin main content area
                        const oktaContent = document.querySelector('.admin-app-main-content, .content-area, [class*="content-wrap"], main, [role="main"]');
                        if (oktaContent && oktaContent.scrollHeight > oktaContent.clientHeight) {
                            oktaContent.scrollBy(0, delta);
                            return 'content-area';
                        }
                        // Priority 3: Any scrollable container that's not the sidebar
                        const containers = document.querySelectorAll('div, section');
                        for (const c of containers) {
                            if (c.scrollHeight > c.clientHeight + 100 && c.clientHeight > 200 && !c.closest('nav, .sidebar, .sidenav')) {
                                c.scrollBy(0, delta);
                                return 'scrollable-div';
                            }
                        }
                        // Fallback: window
                        window.scrollBy(0, delta);
                        return 'window';
                    }""", delta)
                self.page.wait_for_timeout(500)
                self.capture_frame(f"scroll:{direction}:{pixels}px")
                return f"Scrolled {direction} {pixels}px (target: {target_desc})"
            except Exception as e:
                return f"Scroll error: {e}"
        elif name == "navigate":
            url = args.get("url", "")
            try:
                self.page.goto(url, wait_until="networkidle", timeout=15000)
                self.page.wait_for_timeout(1500)
                result = f"Navigated to {self.page.url}"
            except Exception as e:
                result = f"Navigate error: {e}"
            self.capture_frame(f"navigate:{url[:60]}")
            return result
        elif name == "click":
            selector = args.get("selector", "")
            force = args.get("force", False)
            # Auto-scope to dialog if one is open — prevents clicking behind overlays
            has_dialog = self.page.evaluate(
                f'() => !!document.querySelector(\'{self.DIALOG_CSS}\')'
            )
            if has_dialog:
                # Try clicking within the dialog first
                dialog_selector = f'#simplemodal-container {selector}, [role="dialog"] {selector}, .MuiDialog-root {selector}, dialog {selector}'
                try:
                    loc = self.page.locator(dialog_selector).first
                    loc.scroll_into_view_if_needed(timeout=3000)
                    loc.click(force=force, timeout=3000)
                    self.page.wait_for_timeout(1500)
                    self.capture_frame(f"click(dialog):{selector[:35]}")
                    # Check if dialog is now closed
                    still_open = self.page.evaluate(
                        f'() => !!document.querySelector(\'{self.DIALOG_CSS}\')'
                    )
                    return f"Clicked '{selector}' inside dialog. URL: {self.page.url}" + (" (dialog closed)" if not still_open else " (dialog still open)")
                except Exception:
                    pass  # Fall through to normal click if dialog-scoped click fails
            old_page_count = len(self.context.pages)
            try:
                loc = self.page.locator(selector).first
                try:
                    loc.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass  # Element might already be visible
                loc.click(force=force, timeout=8000)
                self.page.wait_for_timeout(1500)
                if len(self.context.pages) > old_page_count:
                    new_page = self.context.pages[-1]
                    try:
                        new_page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    new_page.wait_for_timeout(1000)
                    self.page = new_page
                    result = f"Clicked '{selector}' — NEW TAB opened [{len(self.context.pages)-1}]: {new_page.url}. You are now on the new tab."
                else:
                    result = f"Clicked '{selector}'. URL: {self.page.url}"
            except Exception as e:
                result = f"Click failed: {e}"
            self.capture_frame(f"click:{selector[:40]}")
            return result
        elif name == "fill":
            selector = args.get("selector", "")
            value = args.get("value", "")
            try:
                self.page.fill(selector, value, timeout=8000)
                result = f"Filled '{selector}'"
            except Exception as e:
                result = f"Fill error: {e}"
            self.capture_frame(f"fill:{selector[:40]}")
            return result
        elif name == "select_option":
            selector = args.get("selector", "")
            value = args.get("value", "")
            try:
                # Try Playwright select_option first (works for native <select>)
                self.page.locator(selector).first.select_option(label=value, timeout=5000)
                # Also trigger change event for Selectize/custom controls
                self.page.locator(selector).first.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                result = f"Selected '{value}' from '{selector}'"
            except Exception:
                try:
                    self.page.locator(selector).first.select_option(value=value, timeout=5000)
                    self.page.locator(selector).first.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                    result = f"Selected value '{value}' from '{selector}'"
                except Exception:
                    # Fallback: use JavaScript to set value and trigger Selectize/Chosen
                    try:
                        self.page.evaluate(f"""(args) => {{
                            const sel = document.querySelector(args.selector);
                            if (!sel) return;
                            // Set native select value
                            const opts = Array.from(sel.options);
                            const match = opts.find(o => o.text.includes(args.value) || o.value.includes(args.value));
                            if (match) {{
                                sel.value = match.value;
                                sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                            }}
                            // Update Selectize if present
                            if (sel.selectize) {{
                                sel.selectize.setValue(match ? match.value : args.value);
                            }}
                            // Update Chosen if present
                            if (window.jQuery && jQuery(sel).data('chosen')) {{
                                jQuery(sel).val(match ? match.value : args.value).trigger('chosen:updated').trigger('change');
                            }}
                            // Force trigger liszt:updated for older Chosen versions
                            try {{ jQuery(sel).trigger('liszt:updated'); }} catch(e) {{}}
                        }}""", {"selector": selector, "value": value})
                        result = f"Selected '{value}' via JS fallback from '{selector}'"
                    except Exception as e:
                        result = f"Select error: {e}"
            self.page.wait_for_timeout(1000)
            self.capture_frame(f"select:{selector[:30]}={value[:20]}")
            return result
        elif name == "get_page_state":
            # Detect open dialog/modal
            has_dialog = self.page.evaluate(
                f'() => !!document.querySelector(\'{self.DIALOG_CSS}\')'
            )
            # Build element query scoped to dialog if one is open
            scope_js = (
                f'(document.querySelector(\'{self.DIALOG_CSS}\') || document)'
                if has_dialog else 'document'
            )
            _GET_ELEMENTS_JS = """(scopeExpr) => {
                const scope = scopeExpr === 'document' ? document : (document.querySelector('#simplemodal-container, [role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]') || document);
                return Array.from(scope.querySelectorAll('a, button, input, select, textarea, [role=button], [role=menuitem], [role=tab], [data-se]'))
                    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < window.innerHeight + 200; })
                    .slice(0, 80)
                    .map((el, idx) => {
                        const t = el.tagName.toLowerCase();
                        const text = (el.textContent||'').trim().replace(/\\s+/g,' ').substring(0,50);
                        const href = el.getAttribute('href')||'';
                        const se = el.getAttribute('data-se')||'';
                        const nm = el.getAttribute('name')||'';
                        const id = el.getAttribute('id')||'';
                        const type = el.getAttribute('type')||'';
                        const placeholder = el.getAttribute('placeholder')||'';
                        const value = el.value||'';
                        let label = '';
                        if (id) { const lbl = document.querySelector('label[for="'+id+'"]'); if (lbl) label = lbl.textContent.trim().substring(0,40); }
                        // Row/parent context: find nearest tr, li, or section ancestor
                        let rowCtx = '';
                        const row = el.closest('tr, li.o-rule-item, [class*=rule], [class*=row], [data-se]');
                        if (row && row !== el) {
                            const rowText = row.textContent.trim().replace(/\\s+/g,' ').substring(0,60);
                            if (rowText && rowText !== text) rowCtx = ' (in row: "'+rowText+'")';
                        }
                        let d = t;
                        if (type) d += '[type='+type+']';
                        if (id) d += '#'+id;
                        if (nm) d += '[name='+nm+']';
                        if (se) d += '[data-se='+se+']';
                        if (label) d += ' label="'+label+'"';
                        if (placeholder) d += ' placeholder="'+placeholder+'"';
                        if ((t === 'input' || t === 'textarea' || t === 'select') && value) d += ' value="'+value.substring(0,30)+'"';
                        if (href && href!=='#') d += ' href="'+href.substring(0,60)+'"';
                        if (text && t !== 'input' && t !== 'textarea') d += ' "'+text+'"';
                        if (rowCtx) d += rowCtx;
                        return d;
                    });
            }"""
            elements = self.page.evaluate(_GET_ELEMENTS_JS, "dialog" if has_dialog else "document")
            dialog_note = ""
            if has_dialog:
                dialog_note = "\n⚠ A DIALOG/MODAL is open. Elements shown are ONLY from inside the dialog. Read the dialog content and interact with its buttons (Close, OK, Save, etc.) before trying to reach elements behind it.\n"
            return f"URL: {self.page.url}\nTitle: {self.page.title()}\n{dialog_note}\nInteractive elements ({len(elements)}):\n" + "\n".join(f"  - {e}" for e in elements)
        elif name == "get_page_text":
            selector = args.get("selector")
            try:
                if selector:
                    text_content = self.page.locator(selector).first.inner_text(timeout=5000)
                else:
                    text_content = self.page.inner_text("body")
                if len(text_content) > 4000:
                    text_content = text_content[:4000] + "\n... (truncated)"
                return text_content
            except Exception as e:
                return f"Error getting text: {e}"
        elif name == "list_tabs":
            tabs = []
            for i, p in enumerate(self.context.pages):
                active = " (ACTIVE)" if p == self.page else ""
                tabs.append(f"  [{i}] {p.url[:80]} — {p.title()[:40]}{active}")
            return f"Open tabs ({len(self.context.pages)}):\n" + "\n".join(tabs)
        elif name == "switch_tab":
            tab_idx = args.get("tab_index", 0)
            pages = self.context.pages
            if 0 <= tab_idx < len(pages):
                self.page = pages[tab_idx]
                self.page.bring_to_front()
                self.page.wait_for_timeout(1000)
                self.capture_frame(f"switch_tab:{tab_idx}")
                return f"Switched to tab [{tab_idx}]: {self.page.url}"
            return f"Invalid tab index {tab_idx}. Have {len(pages)} tabs (0-{len(pages)-1})."
        elif name == "wait_for_new_tab":
            timeout_ms = args.get("timeout", 10000)
            try:
                new_page = self.context.wait_for_event("page", timeout=timeout_ms)
                new_page.wait_for_load_state("networkidle", timeout=15000)
                new_page.wait_for_timeout(1500)
                self.page = new_page
                new_idx = len(self.context.pages) - 1
                self.capture_frame(f"new_tab:{new_page.url[:50]}")
                return f"New tab opened [{new_idx}]: {new_page.url} — {new_page.title()}"
            except Exception as e:
                return f"No new tab opened within {timeout_ms}ms. Current tabs: {len(self.context.pages)}"
        elif name == "wait":
            ms = args.get("milliseconds", 2000)
            sel = args.get("selector")
            if sel:
                try:
                    self.page.wait_for_selector(sel, timeout=ms)
                    return f"Selector '{sel}' appeared"
                except:
                    return f"Selector '{sel}' not found in {ms}ms"
            self.page.wait_for_timeout(ms)
            return f"Waited {ms}ms"
        elif name == "browser_api":
            method = args.get("method", "GET")
            path = args.get("path", "")
            body = args.get("body")
            self._log(f"  🔌 API: {method} {path[:80]}")

            try:
                # Strategy 1: If we have an SSWS API key, use Python urllib (server-side)
                # Browser fetch() with SSWS hits CORS — must be server-side
                if self._okta_api_key:
                    import urllib.request
                    import urllib.error

                    api_key = self._okta_api_key
                    if not api_key.startswith("SSWS "):
                        api_key = f"SSWS {api_key}"

                    # Get the base Okta domain from the admin console tab URL
                    api_origin = ""
                    for p in self.context.pages:
                        url = p.url
                        if "-admin." in url and ".okta.com" in url:
                            # Extract: https://demo-org-admin.okta.com/... → https://demo-org.okta.com
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            base_host = parsed.hostname.replace("-admin.", ".")
                            api_origin = f"{parsed.scheme}://{base_host}"
                            break
                    if not api_origin:
                        # Fallback: try to derive from admin_url
                        api_origin = self.admin_url.replace("-admin.", ".").rstrip("/")
                        if not api_origin.startswith("http"):
                            api_origin = f"https://{api_origin}"

                    full_url = f"{api_origin}{path}"
                    self._log(f"  🔌 API via SSWS token: {full_url[:80]}")

                    req_body = json.dumps(body).encode() if body and method in ("POST", "PUT") else None
                    req = urllib.request.Request(full_url, data=req_body, method=method)
                    req.add_header("Authorization", api_key)
                    req.add_header("Accept", "application/json")
                    req.add_header("Content-Type", "application/json")

                    try:
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            resp_body = resp.read().decode()
                            status = resp.status
                    except urllib.error.HTTPError as e:
                        resp_body = e.read().decode() if e.fp else ""
                        status = e.code
                    except Exception as e:
                        self._log(f"  🔌 SSWS request failed: {e}")
                        status = 0
                        resp_body = str(e)

                    try:
                        data = json.loads(resp_body) if resp_body else {}
                    except json.JSONDecodeError:
                        data = resp_body[:2000]

                    # Auto-activate TOTP factors: if we just enrolled a factor and got
                    # PENDING_ACTIVATION with a sharedSecret, activate it automatically
                    if (status == 200 and isinstance(data, dict)
                            and data.get("status") == "PENDING_ACTIVATION"
                            and data.get("factorType") == "token:software:totp"):
                        embedded = data.get("_embedded", {})
                        activation = embedded.get("activation", {})
                        shared_secret = activation.get("sharedSecret")
                        factor_id = data.get("id")
                        user_match = path.split("/users/")[1].split("/")[0] if "/users/" in path else None

                        if shared_secret and factor_id and user_match:
                            self._log(f"  🔌 Auto-activating TOTP factor {factor_id} with secret {shared_secret[:4]}...")
                            try:
                                import pyotp
                                totp_code = pyotp.TOTP(shared_secret).now()
                                activate_url = f"{api_origin}/api/v1/users/{user_match}/factors/{factor_id}/lifecycle/activate"
                                activate_body = json.dumps({"passCode": totp_code}).encode()
                                activate_req = urllib.request.Request(activate_url, data=activate_body, method="POST")
                                activate_req.add_header("Authorization", api_key)
                                activate_req.add_header("Accept", "application/json")
                                activate_req.add_header("Content-Type", "application/json")
                                with urllib.request.urlopen(activate_req, timeout=15) as activate_resp:
                                    activate_data = json.loads(activate_resp.read().decode())
                                    activate_status = activate_data.get("status", "?")
                                    self._log(f"  🔌 Factor activation: {activate_status}")
                                    if activate_status == "ACTIVE":
                                        data_str = json.dumps(activate_data, indent=2)
                                        if len(data_str) > 3000:
                                            data_str = data_str[:3000] + "\n... (truncated)"
                                        return f"API {method} {path} → 200 (via SSWS token)\nFactor enrolled AND activated successfully!\nShared secret: {shared_secret}\nStatus: ACTIVE\n{data_str}"
                            except Exception as e:
                                self._log(f"  🔌 Auto-activation failed: {e}")

                    data_str = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
                    if len(data_str) > 3000:
                        data_str = data_str[:3000] + "\n... (truncated)"
                    self._log(f"  🔌 API response (SSWS): {status}")
                    return f"API {method} {path} → {status} (via SSWS token)\n{data_str}"

                # Strategy 2: Session-based fetch from browser tabs
                pages_to_try = []
                admin_page = None
                base_page = None
                for p in self.context.pages:
                    url = p.url
                    if "-admin." in url or "/admin/" in url:
                        admin_page = p
                    elif ".okta.com" in url and "-admin." not in url and "labs." not in url and "auth." not in url:
                        base_page = p

                body_json = json.dumps(body) if body else "null"
                _FETCH_JS = f"""async () => {{
                    const headers = {{"Accept": "application/json", "Content-Type": "application/json"}};
                    const opts = {{method: "{method}", headers: headers, credentials: "same-origin"}};
                    if ("{method}" !== "GET" && {body_json} !== null) opts.body = JSON.stringify({body_json});
                    try {{
                        const resp = await fetch("{path}", opts);
                        const text = await resp.text();
                        try {{ return {{ok: true, status: resp.status, data: JSON.parse(text)}}; }}
                        catch {{ return {{ok: true, status: resp.status, data: text.substring(0, 2000)}}; }}
                    }} catch(e) {{
                        return {{ok: false, error: e.message}};
                    }}
                }}"""

                if admin_page:
                    pages_to_try.append(("admin", admin_page))
                if base_page:
                    pages_to_try.append(("base-domain", base_page))
                if not pages_to_try:
                    pages_to_try.append(("current", self.page))

                for tab_name, page in pages_to_try:
                    self._log(f"  🔌 Trying {tab_name} tab: {page.url[:60]}")
                    result = page.evaluate(_FETCH_JS)

                    if not result.get("ok"):
                        err = result.get("error", "unknown")
                        self._log(f"  🔌 {tab_name} failed: {err}")
                        continue

                    status = result.get("status", "?")
                    data = result.get("data", {})

                    # If 403 with empty body, try next tab
                    if status == 403 and not data and tab_name != pages_to_try[-1][0]:
                        self._log(f"  🔌 {tab_name} got 403 — trying next tab")
                        continue

                    data_str = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
                    if len(data_str) > 3000:
                        data_str = data_str[:3000] + "\n... (truncated)"
                    self._log(f"  🔌 API response via {tab_name}: {status}")
                    return f"API {method} {path} → {status} (via {tab_name} session)\n{data_str}"

                return f"API error: all tabs failed for {method} {path}. Consider providing an Okta API key in the app UI."
            except Exception as e:
                return f"API error: {e}"
        elif name == "inspect_element":
            selector = args.get("selector", "")
            self._log(f"  🔍 Inspecting: {selector[:60]}")
            try:
                info = self.page.evaluate("""(selector) => {
                    const el = document.querySelector(selector) ||
                        (() => { try { return document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; } catch(e) { return null; } })();
                    if (!el) return {found: false, error: 'Element not found with CSS selector: ' + selector};

                    const rect = el.getBoundingClientRect();
                    const tag = el.tagName.toLowerCase();

                    // Check what element is actually at this position (obstruction check)
                    const centerX = rect.left + rect.width / 2;
                    const centerY = rect.top + rect.height / 2;
                    const topEl = document.elementFromPoint(centerX, centerY);
                    let obstruction = null;
                    if (topEl && topEl !== el && !el.contains(topEl)) {
                        obstruction = {
                            tag: topEl.tagName.toLowerCase(),
                            id: topEl.id || null,
                            classes: topEl.className || null,
                            text: (topEl.textContent || '').trim().substring(0, 50)
                        };
                    }

                    // Parent chain (up to 5 levels)
                    const parents = [];
                    let p = el.parentElement;
                    for (let i = 0; i < 5 && p; i++) {
                        const pInfo = p.tagName.toLowerCase();
                        const pId = p.id ? '#' + p.id : '';
                        const pClass = p.className ? '.' + String(p.className).split(' ').filter(c=>c).slice(0,3).join('.') : '';
                        const pRole = p.getAttribute('role') ? '[role=' + p.getAttribute('role') + ']' : '';
                        parents.push(pInfo + pId + pClass + pRole);
                        p = p.parentElement;
                    }

                    // Sibling elements
                    const siblings = [];
                    if (el.parentElement) {
                        Array.from(el.parentElement.children).forEach((sib, idx) => {
                            if (siblings.length < 5) {
                                const isCurrent = sib === el ? ' ← THIS' : '';
                                const sibTag = sib.tagName.toLowerCase();
                                const sibText = (sib.textContent || '').trim().substring(0, 40);
                                siblings.push(idx + ': ' + sibTag + ' "' + sibText + '"' + isCurrent);
                            }
                        });
                    }

                    // Suggested selectors
                    const suggestions = [];
                    if (el.id) suggestions.push('#' + el.id);
                    if (el.getAttribute('data-se')) suggestions.push('[data-se="' + el.getAttribute('data-se') + '"]');
                    if (el.getAttribute('data-testid')) suggestions.push('[data-testid="' + el.getAttribute('data-testid') + '"]');
                    const text = (el.textContent || '').trim();
                    if (text && text.length < 30) suggestions.push(tag + ':has-text("' + text + '")');
                    if (el.getAttribute('href')) suggestions.push(tag + '[href="' + el.getAttribute('href') + '"]');

                    return {
                        found: true,
                        tag: tag,
                        id: el.id || null,
                        classes: el.className || null,
                        attributes: {
                            type: el.getAttribute('type'),
                            role: el.getAttribute('role'),
                            'data-se': el.getAttribute('data-se'),
                            href: el.getAttribute('href'),
                            disabled: el.disabled || null,
                            'aria-label': el.getAttribute('aria-label'),
                            'aria-hidden': el.getAttribute('aria-hidden'),
                        },
                        text: text.substring(0, 100),
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)},
                        visible: rect.width > 0 && rect.height > 0,
                        inViewport: rect.top < window.innerHeight && rect.bottom > 0,
                        obstruction: obstruction,
                        parentChain: parents,
                        siblings: siblings,
                        suggestedSelectors: suggestions,
                        computedStyle: {
                            display: getComputedStyle(el).display,
                            visibility: getComputedStyle(el).visibility,
                            opacity: getComputedStyle(el).opacity,
                            pointerEvents: getComputedStyle(el).pointerEvents,
                            zIndex: getComputedStyle(el).zIndex,
                        }
                    };
                }""", selector)

                if not info.get("found"):
                    # Try with Playwright locator as fallback
                    try:
                        loc = self.page.locator(selector).first
                        if loc.count() > 0:
                            tag = loc.evaluate("el => el.tagName.toLowerCase()")
                            text = loc.evaluate("el => (el.textContent || '').trim().substring(0, 100)")
                            return f"Element found via Playwright locator but not CSS. Tag: {tag}, Text: '{text}'. Try using Playwright-style selectors like 'text={text}' or '{tag}:has-text(\"{text}\")'."
                    except Exception:
                        pass
                    return f"Element not found: '{selector}'. Try get_page_state to see available elements."

                # Format the inspection report
                lines = [f"=== Inspecting: {selector} ==="]
                lines.append(f"Tag: <{info['tag']}> | ID: {info.get('id') or '(none)'} | Classes: {info.get('classes') or '(none)'}")
                lines.append(f"Text: \"{info.get('text', '')}\"")
                lines.append(f"Position: ({info['rect']['x']}, {info['rect']['y']}) Size: {info['rect']['w']}x{info['rect']['h']}")
                lines.append(f"Visible: {info['visible']} | In viewport: {info['inViewport']}")

                attrs = {k: v for k, v in info.get('attributes', {}).items() if v}
                if attrs:
                    lines.append(f"Attributes: {attrs}")

                style = info.get('computedStyle', {})
                if style.get('pointerEvents') == 'none':
                    lines.append(f"⚠ pointer-events: none — clicks will pass through this element!")
                if style.get('opacity') == '0':
                    lines.append(f"⚠ opacity: 0 — element is invisible!")
                if style.get('display') == 'none':
                    lines.append(f"⚠ display: none — element is hidden!")

                if info.get('obstruction'):
                    obs = info['obstruction']
                    lines.append(f"⚠ OBSTRUCTED by: <{obs['tag']}> id={obs.get('id')} class={obs.get('classes', '')[:50]}")
                    lines.append(f"  Obstruction text: \"{obs.get('text', '')}\"")
                    lines.append(f"  → Try click with force=true, or click the obstructing element first")
                else:
                    lines.append(f"✓ Not obstructed — element is clickable at its center point")

                lines.append(f"Parent chain: {' > '.join(info.get('parentChain', []))}")

                if info.get('siblings'):
                    lines.append(f"Siblings in parent:")
                    for sib in info['siblings']:
                        lines.append(f"  {sib}")

                if info.get('suggestedSelectors'):
                    lines.append(f"Suggested selectors:")
                    for s in info['suggestedSelectors']:
                        lines.append(f"  - {s}")

                return "\n".join(lines)
            except Exception as e:
                return f"Inspect error: {e}"
        return f"Unknown tool: {name}"
