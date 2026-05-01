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

    def record_guide(self, guide_text: str, max_iterations: int = 40) -> Recording:
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

        markers = parse_markers(guide_text)
        self._log(f"Found {len(markers)} screenshot markers")

        # Capture initial frame
        self.capture_frame("session_start")

        # Use LLM to drive navigation
        try:
            self._drive_with_llm(clean_text, markers, max_iterations)
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

    def _drive_with_llm(self, guide_text: str, markers, max_iterations: int):
        """Use LLM to navigate through guide steps, capturing frames at each action."""
        try:
            from litellm import completion
        except ImportError:
            self._log("litellm not available — capturing initial frame only")
            return

        model_id = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")

        # Simpler tool set — just navigation, no screenshot decisions
        tools = [
            {
                "name": "navigate",
                "description": "Navigate to a full URL. Only use this when you know the exact URL — prefer clicking links and buttons instead.",
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "Full URL to navigate to"}},
                    "required": ["url"]
                }
            },
            {
                "name": "click",
                "description": "Click an element on the page. Use text-based selectors for best results. Examples: 'text=Security', 'text=System Log', 'a:has-text(\"Reports\")', 'button:has-text(\"Save\")', 'button:has-text(\"Launch\")', '[data-se=\"save\"]'. For navigation menus, click the section header first, wait, then click sub-items.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string"},
                        "force": {"type": "boolean"}
                    },
                    "required": ["selector"]
                }
            },
            {
                "name": "fill",
                "description": "Fill an input field with text. IMPORTANT: To target the correct field, use precise selectors based on the field's name, id, or label. Examples: 'input[name=\"org_name\"]', 'input#org-name', '#session-timeout'. Always call get_page_state first to see available input fields and their name/id attributes, then use those for targeting.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "Playwright selector — use name or id attributes for precision: input[name=\"field_name\"], input#field-id"},
                        "value": {"type": "string", "description": "Text to type into the field"}
                    },
                    "required": ["selector", "value"]
                }
            },
            {
                "name": "get_page_state",
                "description": "Get current URL, title, and visible interactive elements.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "get_page_text",
                "description": "Get the visible text content of the current page or a specific section. Useful for reading instructions, finding specific text, or verifying page content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "Optional CSS selector to scope text. Omit for full page."}
                    }
                }
            },
            {
                "name": "wait",
                "description": "Wait milliseconds or for a selector.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "milliseconds": {"type": "integer"},
                        "selector": {"type": "string"}
                    }
                }
            },
            {
                "name": "list_tabs",
                "description": "List all open browser tabs with their index, URL, and title. Use this to see what tabs are available after clicking a link that opened a new tab.",
                "input_schema": {"type": "object", "properties": {}}
            },
            {
                "name": "switch_tab",
                "description": "Switch to a different browser tab by index number. After switching, call get_page_state to see the new tab's content. Use list_tabs first to see available tabs.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tab_index": {"type": "integer", "description": "0-based index of the tab to switch to (from list_tabs output)"}
                    },
                    "required": ["tab_index"]
                }
            },
            {
                "name": "wait_for_new_tab",
                "description": "Wait for a new tab to open (e.g., after clicking a Launch button or a link with target=_blank). Returns the new tab's URL and index. Call this BEFORE clicking if you expect a new tab to open, then click, then this will capture the new tab.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "timeout": {"type": "integer", "description": "Max milliseconds to wait for new tab. Default 10000."}
                    }
                }
            },
            {
                "name": "done",
                "description": "Signal you've visited all the pages referenced in the guide.",
                "input_schema": {"type": "object", "properties": {}}
            },
        ]

        marker_pages = "\n".join(f"  [{m.index}] {m.description}" for m in markers)

        system = f"""You are a browser automation agent. Your job is to follow the step-by-step instructions in a lab guide, executing each action exactly as described.

You are controlling a real browser. A human user has already authenticated and set up the session for you. Your starting URL is whatever page the browser is currently on.

A screenshot is captured automatically after every action you take. You do NOT need to take screenshots — just follow the instructions.

## CRITICAL: Do NOT guess URLs
You are already on the correct starting page. A human user authenticated and navigated there for you.
- NEVER type URLs directly into the navigate tool unless the guide explicitly gives you a URL to go to
- ALWAYS use click-based navigation — click links, buttons, and menu items that are visible on the current page
- If you need to go somewhere, look for a matching link/button on the page and click it
- Do NOT try to guess domain names or construct URLs

## Your approach
1. FIRST, call get_page_state to see where you are and what's on screen — this is your starting point, DO NOT navigate away
2. Read the guide instructions carefully and execute them IN ORDER
3. For each step:
   - Read what the step says to do (click, navigate, fill in, select, etc.)
   - Look at the current page state to find the right element
   - Execute the action using the appropriate tool
   - Call get_page_state after each action to verify it worked
4. If a step says "navigate to X" or "go to X", look for a link or menu item matching X on the current page and click it — don't guess URLs
5. If a step says "click X", find the element with matching text and click it
6. If a step says "fill in X with Y", FIRST call get_page_state to see all input fields with their name/id attributes, THEN use fill with a precise selector like input#field-id or input[name="field_name"]. Fill each field individually with its own fill call.
7. If something doesn't work, try alternative selectors or approaches
8. Call done when you've completed all the steps

## Key rules
- Follow the guide instructions literally — do what they say, in the order they say it
- Use click navigation (clicking links, buttons, menu items) rather than typing URLs directly
- When looking for elements, use text-based selectors: 'text=Security', 'a:has-text("System Log")', 'button:has-text("Save")'
- If the page has a sidebar or navigation menu, use it to navigate
- If a button opens a new panel, dialog, or section, wait for it to load before proceeding
- Some steps may refer to actions you can't perform (external tools, mobile devices, etc.) — skip those and move to the next step you CAN do in the browser
- The guide may have [SCREENSHOT: ...] markers — these are just placeholders, ignore them and keep following the instructions

## Multi-tab / multi-window support
Some actions (like clicking "Launch" buttons or links with target=_blank) will open NEW BROWSER TABS. When this happens:
- Use wait_for_new_tab AFTER clicking a button that opens a new tab — it will automatically switch you to the new tab
- Use list_tabs to see all open tabs and their URLs
- Use switch_tab to move between tabs (e.g., go back to the lab guide tab, then switch to the Okta admin tab)
- After switching tabs, always call get_page_state to see the new tab's content
- The recording captures frames from whichever tab is currently active

## Loading states and spinners
Some actions trigger loading states (progress bars, spinners, "loading..." text). When you see these:
- Call wait with 5000-15000 milliseconds to let the content load
- Then call get_page_state to check if loading is complete
- If you still see a loading indicator, wait again
- Common loading patterns: progress bars, spinning icons, "Loading...", "Please wait...", skeleton screens

## Screenshots needed at these points in the guide
{marker_pages}

Make sure you reach the pages/views described above during your navigation. The recording system captures a frame at every action, so just being on the right page is sufficient."""

        messages = [{"role": "user", "content": f"Navigate through this guide:\n\n{guide_text}"}]
        litellm_kwargs = {"model": model_id, "messages": messages, "tools": tools, "max_tokens": 4096}
        if os.environ.get("LITELLM_API_BASE"):
            litellm_kwargs["api_base"] = os.environ["LITELLM_API_BASE"]
            litellm_kwargs["api_key"] = os.environ.get("LITELLM_API_KEY", "")

        for iteration in range(max_iterations):
            self._log(f"nav iteration {iteration + 1}/{max_iterations}")

            try:
                response = completion(**litellm_kwargs, system=system)
            except Exception as e:
                self._log(f"LLM error: {e}")
                break

            message = response.choices[0].message
            messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})

            if not message.tool_calls:
                break

            tool_results = []
            is_done = False

            for tc in message.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                if name == "done":
                    is_done = True
                    result = "Navigation complete."
                elif name == "navigate":
                    url = args.get("url", "")
                    try:
                        self.page.goto(url, wait_until="networkidle", timeout=15000)
                        self.page.wait_for_timeout(1500)
                        result = f"Navigated to {self.page.url}"
                    except Exception as e:
                        result = f"Navigate error: {e}"
                    self.capture_frame(f"navigate:{url[:60]}")
                elif name == "click":
                    selector = args.get("selector", "")
                    force = args.get("force", False)
                    old_page_count = len(self.context.pages)
                    try:
                        self.page.locator(selector).first.click(force=force, timeout=8000)
                        self.page.wait_for_timeout(1500)
                        # Check if a new tab was opened by the click
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
                elif name == "fill":
                    selector = args.get("selector", "")
                    value = args.get("value", "")
                    try:
                        self.page.fill(selector, value, timeout=8000)
                        result = f"Filled '{selector}'"
                    except Exception as e:
                        result = f"Fill error: {e}"
                    self.capture_frame(f"fill:{selector[:40]}")
                elif name == "get_page_state":
                    elements = self.page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a, button, input, select, textarea, [role=button], [role=menuitem], [role=tab], [data-se]'))
                            .filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.top < window.innerHeight; })
                            .slice(0, 80)
                            .map(el => {
                                const t = el.tagName.toLowerCase();
                                const text = (el.textContent||'').trim().replace(/\\s+/g,' ').substring(0,50);
                                const href = el.getAttribute('href')||'';
                                const se = el.getAttribute('data-se')||'';
                                const name = el.getAttribute('name')||'';
                                const id = el.getAttribute('id')||'';
                                const type = el.getAttribute('type')||'';
                                const placeholder = el.getAttribute('placeholder')||'';
                                const value = el.value||'';
                                // Find associated label
                                let label = '';
                                if (id) {
                                    const lbl = document.querySelector('label[for="'+id+'"]');
                                    if (lbl) label = lbl.textContent.trim().substring(0,40);
                                }
                                let d = t;
                                if (type) d += '[type='+type+']';
                                if (id) d += '#'+id;
                                if (name) d += '[name='+name+']';
                                if (se) d += '[data-se='+se+']';
                                if (label) d += ' label="'+label+'"';
                                if (placeholder) d += ' placeholder="'+placeholder+'"';
                                if (t === 'input' || t === 'textarea' || t === 'select') {
                                    if (value) d += ' value="'+value.substring(0,30)+'"';
                                }
                                if (href && href!=='#') d += ' href="'+href.substring(0,60)+'"';
                                if (text && t !== 'input' && t !== 'textarea') d += ' "'+text+'"';
                                return d;
                            });
                    }""")
                    result = f"URL: {self.page.url}\nTitle: {self.page.title()}\n\nInteractive elements ({len(elements)}):\n" + "\n".join(f"  - {e}" for e in elements)
                elif name == "get_page_text":
                    selector = args.get("selector")
                    try:
                        if selector:
                            text_content = self.page.locator(selector).first.inner_text(timeout=5000)
                        else:
                            text_content = self.page.inner_text("body")
                        if len(text_content) > 4000:
                            text_content = text_content[:4000] + "\n... (truncated)"
                        result = text_content
                    except Exception as e:
                        result = f"Error getting text: {e}"
                elif name == "list_tabs":
                    tabs = []
                    for i, p in enumerate(self.context.pages):
                        active = " (ACTIVE)" if p == self.page else ""
                        tabs.append(f"  [{i}] {p.url[:80]} — {p.title()[:40]}{active}")
                    result = f"Open tabs ({len(self.context.pages)}):\n" + "\n".join(tabs)
                elif name == "switch_tab":
                    tab_idx = args.get("tab_index", 0)
                    pages = self.context.pages
                    if 0 <= tab_idx < len(pages):
                        self.page = pages[tab_idx]
                        self.page.bring_to_front()
                        self.page.wait_for_timeout(1000)
                        result = f"Switched to tab [{tab_idx}]: {self.page.url}"
                        self.capture_frame(f"switch_tab:{tab_idx}")
                    else:
                        result = f"Invalid tab index {tab_idx}. Have {len(pages)} tabs (0-{len(pages)-1})."
                elif name == "wait_for_new_tab":
                    timeout_ms = args.get("timeout", 10000)
                    old_count = len(self.context.pages)
                    try:
                        new_page = self.context.wait_for_event("page", timeout=timeout_ms)
                        new_page.wait_for_load_state("networkidle", timeout=15000)
                        new_page.wait_for_timeout(1500)
                        self.page = new_page
                        new_idx = len(self.context.pages) - 1
                        result = f"New tab opened [{new_idx}]: {new_page.url} — {new_page.title()}"
                        self.capture_frame(f"new_tab:{new_page.url[:50]}")
                    except Exception as e:
                        result = f"No new tab opened within {timeout_ms}ms. Current tabs: {len(self.context.pages)}"
                elif name == "wait":
                    ms = args.get("milliseconds", 2000)
                    sel = args.get("selector")
                    if sel:
                        try:
                            self.page.wait_for_selector(sel, timeout=ms)
                            result = f"Selector '{sel}' appeared"
                        except:
                            result = f"Selector '{sel}' not found in {ms}ms"
                    else:
                        self.page.wait_for_timeout(ms)
                        result = f"Waited {ms}ms"
                else:
                    result = f"Unknown tool: {name}"

                tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            messages.extend(tool_results)
            litellm_kwargs["messages"] = messages

            if is_done:
                break

        self._log(f"Navigation complete: {len(self.recording.frames)} frames captured")
