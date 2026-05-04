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
    ):
        self.page = page
        self.context = context
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
        {"name": "get_page_state", "description": "Get current URL, title, and visible interactive elements with their selectors.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "get_page_text", "description": "Get visible text of current page or a CSS-scoped section.", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}}},
        {"name": "wait", "description": "Wait milliseconds or for a CSS selector to appear.", "input_schema": {"type": "object", "properties": {"milliseconds": {"type": "integer"}, "selector": {"type": "string"}}}},
        {"name": "list_tabs", "description": "List all open browser tabs.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "switch_tab", "description": "Switch to tab by index. Use list_tabs first.", "input_schema": {"type": "object", "properties": {"tab_index": {"type": "integer"}}, "required": ["tab_index"]}},
        {"name": "wait_for_new_tab", "description": "Wait for a new tab to open after clicking a link/button.", "input_schema": {"type": "object", "properties": {"timeout": {"type": "integer"}}}},
        {"name": "section_complete", "description": "Signal that the current section's goal has been achieved. Call this when you can see the success condition in the screenshot.", "input_schema": {"type": "object", "properties": {}}},
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
        # Track actions globally so repeated buttons are blocked across sections
        global_actions = []
        total_iters_used = 0
        iters_per_section = max(10, max_iterations // max(len(sections), 1))

        for i, section in enumerate(sections):
            if section.get("skip_reason"):
                self._log(f"SKIP section {i+1}/{len(sections)}: {section['title']} — {section['skip_reason']}")
                continue

            self._log(f"=== Section {i+1}/{len(sections)}: {section['title']} ===")
            iters_used = self._execute_section(section, max_iterations=iters_per_section, global_actions=global_actions)
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
- If a section has a button that triggers an async operation (like running a simulation), the steps should be: click the button, wait for results, observe the outcome
- Be specific in steps: "Click the Execute button in the attack simulator panel" not "Run the simulation"
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

    def _execute_section(self, section: dict, max_iterations: int = 25, global_actions: list = None) -> int:
        """Execute one section of the guide. Returns iterations used."""

        title = section["title"]
        goal = section["goal"]
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(section["steps"]))
        success = section["success_looks_like"]
        markers = section.get("screenshot_markers", [])

        system = f"""You are a browser automation agent executing ONE section of a lab guide.

## YOUR CURRENT GOAL
{goal}

## STEPS TO FOLLOW
{steps}

## YOU ARE DONE WHEN
{success}

When you can see the success condition in the screenshot, call section_complete immediately.

## VISUAL FEEDBACK
After every action, you receive a screenshot. Use it to:
- Verify your action worked (did the page change?)
- Understand the layout (sidebar, main content, panels, dialogs)
- Check if the success condition is met
- Find the right elements for the next step

## RULES
- Execute steps in order. After each action, check the screenshot.
- Use click-based navigation: 'text=Security', 'button:has-text("Save")', 'a:has-text("Reports")'
- Use get_page_state when you need exact selectors (name/id attributes)
- NEVER click the same button twice — if you clicked it and the page changed, it worked
- If a button triggers an operation (simulation, save, etc.), click ONCE, wait 3-5 seconds, then observe results
- If a step can't be done in the browser (external tool, mobile device), skip it
- If you're stuck, try get_page_state or get_page_text to understand what's on screen
- Call section_complete as soon as the success condition is visible

## IMPORTANT: Check for open tabs
Call list_tabs early to see if relevant tabs are already open (admin console, etc.)."""

        messages = [{"role": "user", "content": f"Execute this section: {title}\n\nGoal: {goal}\nSteps:\n{steps}"}]

        # Include global action history so buttons blocked in earlier sections stay blocked
        recent_actions = list(global_actions) if global_actions else []

        for iteration in range(max_iterations):
            self._log(f"  [{title[:30]}] iteration {iteration + 1}/{max_iterations}")

            # Build messages with ephemeral screenshot
            call_messages = list(messages)
            if iteration > 0:
                try:
                    page_b64 = self._capture_page_b64()
                    # Check for open dialogs
                    has_dialog = self.page.evaluate("""() => {
                        const d = document.querySelector('[role="dialog"]:not([aria-hidden="true"]), .MuiDialog-root, .modal.show, dialog[open]');
                        if (!d) return null;
                        return d.innerText.substring(0, 500);
                    }""")
                    hint = "Screenshot of the current page."
                    if has_dialog:
                        hint += f"\n\n⚠ A DIALOG/MODAL is open over the page. Read its content carefully — it may show results, a form, or a confirmation. The dialog text is:\n\"{has_dialog[:300]}\"\n\nInteract with the dialog (read it, click its buttons like Close/OK/Save) before trying anything behind it."
                    hint += "\n\nCheck if the success condition is met. If yes, call section_complete. Otherwise, continue with the next step."

                    call_messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": hint},
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
            messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})

            if not message.tool_calls:
                break

            tool_results = []
            section_done = False

            for tc in message.tool_calls:
                result = self._execute_tool(tc, recent_actions)
                if tc.function.name == "section_complete":
                    section_done = True
                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.extend(tool_results)

            if section_done:
                self._log(f"  [{title[:30]}] complete ✓")
                break
        else:
            self._log(f"  [{title[:30]}] max iterations reached")

        # Feed section actions back into global history
        if global_actions is not None:
            global_actions.extend(recent_actions)

        return iteration + 1

    def _execute_tool(self, tc, recent_actions: list) -> str:
        """Execute a single tool call. Returns the result string."""
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            args = {}

        # Stuck detection for click/fill/navigate
        if name in ("click", "fill", "navigate"):
            selector = args.get('selector', args.get('url', ''))
            action_key = f"{name}:{selector[:50]}"

            # Normalize: extract core button/link text for matching
            # 'button:has-text("Execute")' and 'div[role="dialog"] button:has-text("Execute")'
            # both normalize to 'click_text:Execute'
            import re
            text_match = re.search(r'has-text\(["\']([^"\']+)["\']\)', selector)
            core_text = text_match.group(1) if text_match else None

            # Count by normalized text (any selector targeting the same text)
            if core_text and name == "click":
                norm_count = sum(1 for a in recent_actions if core_text.lower() in a.lower())
            else:
                norm_count = recent_actions.count(action_key)

            recent_actions.append(action_key)

            if norm_count >= 2:
                label = core_text or selector[:40]
                self._log(f"  BLOCKED ({norm_count}x): {action_key}")
                return (
                    f"REFUSED: You have already clicked '{label}' {norm_count} times (with various selectors). "
                    f"It completed on the first attempt. The result is visible in the screenshot. "
                    f"Do NOT try again with a different selector — that will also be blocked. "
                    f"Move to the next step or call section_complete if the goal is achieved."
                )

        if name == "section_complete":
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
