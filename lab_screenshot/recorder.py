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
    ):
        self.page = page
        self.context = context
        self._human_input_callback = human_input_callback
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

    def record_guide(self, guide_text: str, max_iterations: int = 100) -> Recording:
        """
        Execute guide steps via LLM and capture frames throughout.
        Returns the complete Recording with all frames.
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
            self._drive_with_llm(clean_text, action_steps, markers, max_iterations)
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

    # -- Browser tool definitions (shared across all execution phases) --
    TOOLS = [
        {"name": "navigate", "description": "Navigate to a full URL. Only use when the guide gives you an explicit URL — prefer clicking links/buttons.", "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "click", "description": "Click an element. Use text selectors: 'text=Security', 'button:has-text(\"Save\")', 'a:has-text(\"Reports\")', '[data-se=\"save\"]'. For menus, click the header first, wait, then sub-items.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["selector"]}},
        {"name": "fill", "description": "Fill an input. Use get_page_state first to find name/id, then: input[name=\"x\"], input#id.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "value": {"type": "string"}}, "required": ["selector", "value"]}},
        {"name": "get_page_state", "description": "Get current URL, title, and visible interactive elements with their selectors. If a dialog/modal is open, shows only dialog elements.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_page_text", "description": "Get visible text of current page or a CSS-scoped section.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}}},
        {"name": "wait", "description": "Wait milliseconds or for a CSS selector to appear.", "input_schema": {"type": "object", "properties": {"milliseconds": {"type": "integer"}, "selector": {"type": "string"}}}},
        {"name": "list_tabs", "description": "List all open browser tabs.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "switch_tab", "description": "Switch to tab by index. Use list_tabs first.", "input_schema": {"type": "object", "properties": {"tab_index": {"type": "integer"}}, "required": ["tab_index"]}},
        {"name": "wait_for_new_tab", "description": "Wait for a new tab to open after clicking a link/button.", "input_schema": {"type": "object", "properties": {"timeout": {"type": "integer"}}}},
        {"name": "section_complete", "description": "Signal that the current section's goal has been achieved. Include a brief reason.", "input_schema": {"type": "object", "properties": {"reason": {"type": "string", "description": "Brief explanation of why this section is complete"}}}},
        {"name": "ask_human", "description": "Ask the human operator for help when you are genuinely stuck or uncertain. Use this when: you've tried 2-3 approaches and none worked, you're unsure which element to interact with, or you need clarification about what the guide means. Do NOT use this as a first resort — try to solve the problem yourself first.", "input_schema": {"type": "object", "properties": {"question": {"type": "string", "description": "Clear question for the human. Describe what you see, what you tried, and what you need help with."}}, "required": ["question"]}},
    ]

    def _drive_with_llm(self, guide_text: str, action_steps: str, markers, max_iterations: int):
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

        # Phase 2: Execute each section
        total_iters_used = 0
        iters_per_section = max(10, max_iterations // max(len(sections), 1))

        for i, section in enumerate(sections):
            if section.get("skip_reason"):
                self._log(f"SKIP section {i+1}/{len(sections)}: {section['title']} — {section['skip_reason']}")
                continue

            self._log(f"=== Section {i+1}/{len(sections)}: {section['title']} ===")
            iters_used = self._execute_section(section, max_iterations=iters_per_section)
            total_iters_used += iters_used

            if total_iters_used >= max_iterations:
                self._log("Max total iterations reached")
                break

        self._log(f"Navigation complete: {len(self.recording.frames)} frames captured in {total_iters_used} iterations")

    def _comprehend_guide(self, guide_text: str, action_steps: str, markers) -> list[dict]:
        """Phase 1: LLM reads the guide and produces a structured execution plan."""
        marker_list = "\n".join(f"  [{m.index}] {m.description}" for m in markers)

        prompt = f"""Read this lab guide and break it into executable sections for a browser automation agent.

For each section, identify:
1. **title**: Short descriptive name
2. **goal**: What should be accomplished (1-2 sentences)
3. **steps**: Specific browser actions needed (click X, navigate to Y, fill in Z, observe W)
4. **success_looks_like**: How to know this section is DONE — what should be visible on screen
5. **screenshot_markers**: Which marker indices should be captured during this section (can be empty)
6. **skip_reason**: Set to a string like "requires mobile device" if the section can't be done in a browser. Set to null if it CAN be done.

## Screenshot markers in the guide:
{marker_list}

## Rules:
- Group related steps by guide heading/section
- Each section needs a CLEAR, OBSERVABLE completion condition
- Some steps involve external tools (mobile devices, physical equipment, virtual desktops) — mark those with skip_reason
- If a section has a button that triggers an async operation (like running a simulation), the steps should include:
  1. Click the button
  2. If a confirmation dialog appears, click the confirm/execute button in the dialog
  3. Wait 5-10 seconds for the operation to complete
  4. Observe whatever appears on screen — that IS the result, even if it doesn't look dramatic
- Be specific in steps: "Click the Execute button in the attack simulator panel" not "Run the simulation"
- For success_looks_like, be PRAGMATIC: if the step is "run an attack", success is "the Execute button has been clicked, any confirmation confirmed, and the page shows the result or returns to its previous state". Do NOT require specific result text you can't know in advance.
- Assign each screenshot marker to exactly one section

## Condensed action steps:
{action_steps}

## Full guide for context:
{guide_text}

Respond with ONLY a JSON object:
{{"sections": [{{"title": "...", "goal": "...", "steps": ["..."], "success_looks_like": "...", "screenshot_markers": [0], "skip_reason": null}}, ...]}}"""

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
                skip = f" [SKIP: {s.get('skip_reason')}]" if s.get('skip_reason') else ""
                markers_str = s.get('screenshot_markers', [])
                self._log(f"  • {s['title']}{skip} (markers: {markers_str})")

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
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(section["steps"]))
        success = section["success_looks_like"]

        system = f"""You are a human tester working through a lab guide in a real browser. You think carefully about each step, observe the results of your actions, and adapt when things don't go as expected.

## YOUR CURRENT TASK
**Goal:** {goal}
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
- **Am I stuck?** If the same thing keeps happening, try a completely different approach. If you've tried 2-3 things and nothing works, call ask_human.

## KEY PRINCIPLES

- **Observe, don't assume.** Look at the screenshot carefully. Read any text, dialogs, messages, or status indicators.
- **Actions have consequences.** After you click something, SOMETHING changed — a dialog opened, a page loaded, content updated, or the action completed silently. Look for the change.
- **Never repeat yourself.** If you clicked a button and the page responded (even by opening a dialog), do NOT click it again. The action worked. Deal with whatever appeared next.
- **Dialogs need attention.** If a dialog/popup is open, interact with IT — don't try to reach elements behind it. Read the dialog text, then click its buttons (Close, OK, Execute, Save, etc.).
- **Know when to move on.** You don't need to see perfect results. If you completed the steps and the page has responded, that's enough. Call section_complete.
- **Ask for help.** If you're genuinely stuck after trying multiple approaches, call ask_human. Describe what you see and what you've tried.

## TOOL TIPS
- Use click with text selectors: `text=Security`, `button:has-text("Save")`, `a:has-text("Reports")`
- Use get_page_state to discover element selectors (name/id attributes)
- Call list_tabs early to find tabs the human may have opened during setup
- Use wait(3000-5000) after actions that trigger async operations"""

        messages = [{"role": "user", "content": f"Execute this section: **{title}**\n\nGoal: {goal}\n\nSteps:\n{steps}\n\nDone when: {success}"}]

        # Cumulative progress log — tracks what the bot has done and observed
        progress_log = []

        for iteration in range(max_iterations):
            self._log(f"  [{title[:30]}] iteration {iteration + 1}/{max_iterations}")

            # Build messages with ephemeral screenshot + progress summary
            call_messages = list(messages)
            if iteration > 0:
                try:
                    page_b64 = self._capture_page_b64()
                    # Detect open dialogs and include their text
                    dialog_text = self.page.evaluate(
                        '() => { const d = document.querySelector(\'[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]\'); return d ? d.innerText.substring(0, 500) : null; }'
                    )

                    # Build progress-aware hint
                    parts = ["Here is the current page."]
                    if dialog_text:
                        parts.append(f'⚠ A DIALOG is open. Text: "{dialog_text[:300]}"')
                    if progress_log:
                        parts.append("## YOUR PROGRESS SO FAR")
                        for entry in progress_log[-8:]:  # Last 8 actions
                            parts.append(f"- {entry}")
                    parts.append("\nBased on your progress and what you see, decide your next action. If you have completed all steps, call section_complete.")

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

            # Log any reasoning the LLM wrote
            if message.content:
                text = message.content if isinstance(message.content, str) else str(message.content)
                for line in text.strip().split('\n')[:3]:
                    self._log(f"  💭 {line[:100]}")

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

                # Record progress for actions that change page state
                if fname in ("click", "fill", "navigate", "switch_tab"):
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    short_result = result[:120]
                    if fname == "click":
                        progress_log.append(f"Clicked '{args.get('selector', '')[:50]}' → {short_result}")
                    elif fname == "fill":
                        progress_log.append(f"Filled '{args.get('selector', '')[:30]}' with '{args.get('value', '')[:20]}'")
                    elif fname == "navigate":
                        progress_log.append(f"Navigated to {args.get('url', '')[:60]}")
                    elif fname == "switch_tab":
                        progress_log.append(f"Switched to tab {args.get('tab_index', '?')} → {short_result}")
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
            self._log(f"  👤 HUMAN: {answer[:100]}")
            return f"Human response: {answer}"
        elif name == "section_complete":
            reason = args.get("reason", "")
            if reason:
                self._log(f"  ✓ Reason: {reason[:100]}")
            return "Section marked complete."
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
                '() => !!document.querySelector(\'[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]\')'
            )
            if has_dialog:
                # Try clicking within the dialog first
                dialog_selector = f'[role="dialog"] {selector}, .MuiDialog-root {selector}, dialog {selector}'
                try:
                    self.page.locator(dialog_selector).first.click(force=force, timeout=3000)
                    self.page.wait_for_timeout(1500)
                    self.capture_frame(f"click(dialog):{selector[:35]}")
                    # Check if dialog is now closed
                    still_open = self.page.evaluate(
                        '() => !!document.querySelector(\'[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]\')'
                    )
                    return f"Clicked '{selector}' inside dialog. URL: {self.page.url}" + (" (dialog closed)" if not still_open else " (dialog still open)")
                except Exception:
                    pass  # Fall through to normal click if dialog-scoped click fails
            old_page_count = len(self.context.pages)
            try:
                self.page.locator(selector).first.click(force=force, timeout=8000)
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
        elif name == "get_page_state":
            # Detect open dialog/modal
            has_dialog = self.page.evaluate(
                '() => !!document.querySelector(\'[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]\')'
            )
            # Build element query scoped to dialog if one is open
            scope_js = (
                '(document.querySelector(\'[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]\') || document)'
                if has_dialog else 'document'
            )
            _GET_ELEMENTS_JS = """(scopeExpr) => {
                const scope = scopeExpr === 'document' ? document : (document.querySelector('[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]') || document);
                return Array.from(scope.querySelectorAll('a, button, input, select, textarea, [role=button], [role=menuitem], [role=tab], [data-se]'))
                    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < window.innerHeight; })
                    .slice(0, 80)
                    .map(el => {
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
        return f"Unknown tool: {name}"
