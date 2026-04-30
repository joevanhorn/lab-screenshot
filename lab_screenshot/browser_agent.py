#!/usr/bin/env python3
"""
browser_agent.py — LLM-driven browser automation for lab guides.

Uses LiteLLM (or direct Anthropic/Bedrock) to read guide steps and drive
Playwright autonomously. The LLM gets browser action tools and the page's
interactive element tree, then decides what to click, fill, and navigate.

When the LLM encounters a [SCREENSHOT: ...] marker in the guide, it
captures the current page state.

LLM Provider Configuration (via environment):
    LITELLM_API_BASE  — LiteLLM proxy URL (e.g., https://litellm.yourcompany.com)
    LITELLM_API_KEY   — API key for LiteLLM proxy
    LLM_MODEL         — Model ID (default: claude-sonnet-4-6)

    Or use direct providers:
    ANTHROPIC_API_KEY  — Direct Anthropic API
    AWS_REGION         — Bedrock (uses bedrock/claude-sonnet-4-6)
"""

import base64
import json
import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Browser action tool definitions (Anthropic tool format)
# ---------------------------------------------------------------------------
BROWSER_TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL. Use for direct URL navigation. Provide a path (e.g., '/admin/dashboard') which gets appended to the admin base URL, or a full URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL or path to navigate to."}
            },
            "required": ["url"]
        }
    },
    {
        "name": "click",
        "description": "Click an element on the page. Use CSS selectors, text selectors, or role selectors. Examples: 'text=Security', 'a:has-text(\"System Log\")', 'button:has-text(\"Save\")', '[data-se=\"save\"]'. For sidebar navigation, click the section header first to expand it, wait, then click the sub-item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Playwright selector for the element to click"},
                "force": {"type": "boolean", "description": "Force click even if element is obscured. Default false."}
            },
            "required": ["selector"]
        }
    },
    {
        "name": "fill",
        "description": "Fill a text input field. Clears existing value first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Playwright selector for the input field"},
                "value": {"type": "string", "description": "Text to type into the field"}
            },
            "required": ["selector", "value"]
        }
    },
    {
        "name": "select_option",
        "description": "Select an option from a dropdown/select element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Selector for the select element"},
                "value": {"type": "string", "description": "Option value or label to select"}
            },
            "required": ["selector", "value"]
        }
    },
    {
        "name": "get_page_state",
        "description": "Get the current page URL, title, and a list of all visible interactive elements (links, buttons, inputs, tabs, menu items). Call this after navigation or whenever you need to understand what's on the page before deciding what to click.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "get_page_text",
        "description": "Get the visible text content of the page or a specific section. Useful for reading content, verifying you're on the right page, or finding specific text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector to scope text extraction. Omit for full page."}
            },
        }
    },
    {
        "name": "wait",
        "description": "Wait for a specified duration or for an element to appear on the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "milliseconds": {"type": "integer", "description": "Time to wait in ms. Default 2000."},
                "selector": {"type": "string", "description": "Optional: wait for this selector to appear instead of fixed time."}
            },
        }
    },
    {
        "name": "capture_screenshot",
        "description": "Capture a PNG screenshot of the current page. Call this ONLY when you have completed the steps leading up to a [SCREENSHOT: ...] marker in the guide.",
        "input_schema": {
            "type": "object",
            "properties": {
                "marker_index": {"type": "integer", "description": "The 0-based index of the [SCREENSHOT] marker this capture is for."}
            },
            "required": ["marker_index"]
        }
    },
    {
        "name": "done",
        "description": "Signal that you have completed processing all steps and captured all screenshots. Call this when there are no more steps to execute.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
]


def _get_llm_client():
    """
    Get a LiteLLM completion function. Supports:
    - LiteLLM proxy (LITELLM_API_BASE + LITELLM_API_KEY)
    - Direct Anthropic (ANTHROPIC_API_KEY)
    - AWS Bedrock (AWS_REGION, uses IAM credentials)
    """
    try:
        from litellm import completion
        return completion
    except ImportError:
        print("ERROR: litellm required. pip install litellm", file=sys.stderr)
        sys.exit(1)


def _get_model_id() -> str:
    """Resolve the model ID based on environment."""
    model = os.environ.get("LLM_MODEL", "")
    if model:
        return model

    # Auto-detect provider
    if os.environ.get("LITELLM_API_BASE"):
        return "claude-sonnet-4-6"  # LiteLLM proxy handles routing
    elif os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-6"
    elif os.environ.get("AWS_REGION") or os.environ.get("AWS_PROFILE"):
        return "bedrock/anthropic.claude-sonnet-4-6"
    else:
        return "claude-sonnet-4-6"


class BrowserAgent:
    """LLM-driven browser automation agent."""

    def __init__(
        self,
        page,  # Playwright page (already authenticated)
        admin_url: str,
        verbose: bool = True,
    ):
        self.page = page
        self.admin_url = admin_url.rstrip("/")
        self.verbose = verbose
        self.images: dict[int, str] = {}
        self.completion = _get_llm_client()
        self.model_id = _get_model_id()

    def _log(self, msg: str):
        if self.verbose:
            print(f"  [agent] {msg}", file=sys.stderr)

    # --- Tool implementations ---

    def _tool_navigate(self, args: dict) -> str:
        url = args["url"]
        if url.startswith("/"):
            url = f"{self.admin_url}{url}"
        self._log(f"navigate → {url}")
        try:
            self.page.goto(url, wait_until="networkidle", timeout=20000)
            self.page.wait_for_timeout(2000)
            return f"Navigated to {self.page.url}"
        except Exception as e:
            return f"Navigation error: {e}"

    def _tool_click(self, args: dict) -> str:
        selector = args["selector"]
        force = args.get("force", False)
        self._log(f"click → {selector}")
        try:
            el = self.page.locator(selector).first
            el.click(force=force, timeout=8000)
            self.page.wait_for_timeout(1500)
            return f"Clicked '{selector}'. Page URL: {self.page.url}"
        except Exception as e:
            return f"Click failed on '{selector}': {e}"

    def _tool_fill(self, args: dict) -> str:
        selector = args["selector"]
        value = args["value"]
        self._log(f"fill → {selector} = {value[:30]}")
        try:
            self.page.fill(selector, value, timeout=8000)
            return f"Filled '{selector}' with '{value}'"
        except Exception as e:
            return f"Fill failed on '{selector}': {e}"

    def _tool_select(self, args: dict) -> str:
        selector = args["selector"]
        value = args["value"]
        self._log(f"select → {selector} = {value}")
        try:
            self.page.select_option(selector, value, timeout=8000)
            return f"Selected '{value}' in '{selector}'"
        except Exception as e:
            return f"Select failed: {e}"

    def _tool_get_page_state(self, args: dict) -> str:
        url = self.page.url
        title = self.page.title()

        elements = self.page.evaluate("""() => {
            const results = [];
            const els = document.querySelectorAll(
                'a, button, input, select, textarea, ' +
                '[role="button"], [role="link"], [role="menuitem"], [role="tab"], ' +
                '[role="treeitem"], [role="option"], [data-se]'
            );
            for (const el of els) {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                if (rect.top > window.innerHeight || rect.bottom < 0) continue;

                const tag = el.tagName.toLowerCase();
                const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 60);
                const href = el.getAttribute('href') || '';
                const type = el.getAttribute('type') || '';
                const name = el.getAttribute('name') || '';
                const role = el.getAttribute('role') || '';
                const dataSe = el.getAttribute('data-se') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const ariaExpanded = el.getAttribute('aria-expanded');

                let desc = tag;
                if (role) desc += `[role=${role}]`;
                if (type) desc += `[type=${type}]`;
                if (name) desc += `[name=${name}]`;
                if (dataSe) desc += `[data-se=${dataSe}]`;
                if (ariaLabel) desc += `[aria-label="${ariaLabel}"]`;
                if (ariaExpanded !== null) desc += `[aria-expanded=${ariaExpanded}]`;
                if (href && href !== '#' && href !== 'javascript:void(0)') desc += ` href="${href.substring(0, 80)}"`;
                if (text) desc += ` "${text}"`;

                results.push(desc);
            }
            return results.slice(0, 100);
        }""")

        result = f"URL: {url}\nTitle: {title}\n\nVisible interactive elements ({len(elements)}):\n"
        for el in elements:
            result += f"  - {el}\n"
        return result

    def _tool_get_page_text(self, args: dict) -> str:
        selector = args.get("selector")
        try:
            if selector:
                text = self.page.locator(selector).first.inner_text(timeout=5000)
            else:
                text = self.page.inner_text("body")
            if len(text) > 4000:
                text = text[:4000] + "\n... (truncated)"
            return text
        except Exception as e:
            return f"Error getting text: {e}"

    def _tool_wait(self, args: dict) -> str:
        ms = args.get("milliseconds", 2000)
        selector = args.get("selector")
        if selector:
            try:
                self.page.wait_for_selector(selector, timeout=ms)
                return f"Element '{selector}' appeared"
            except Exception:
                return f"Element '{selector}' did not appear within {ms}ms"
        else:
            self.page.wait_for_timeout(ms)
            return f"Waited {ms}ms"

    def _tool_capture(self, args: dict) -> str:
        idx = args["marker_index"]
        self._log(f"screenshot → marker {idx}")
        self.page.wait_for_timeout(500)
        png_bytes = self.page.screenshot(type="png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        self.images[idx] = f"data:image/png;base64,{b64}"
        return f"Screenshot captured for marker {idx} ({len(png_bytes):,} bytes)"

    def _execute_tool(self, name: str, args: dict) -> str:
        dispatch = {
            "navigate": self._tool_navigate,
            "click": self._tool_click,
            "fill": self._tool_fill,
            "select_option": self._tool_select,
            "get_page_state": self._tool_get_page_state,
            "get_page_text": self._tool_get_page_text,
            "wait": self._tool_wait,
            "capture_screenshot": self._tool_capture,
            "done": lambda a: "__DONE__",
        }
        fn = dispatch.get(name)
        if not fn:
            return f"Unknown tool: {name}"
        return fn(args)

    # --- Main agentic loop ---

    def process_guide(self, guide_text: str, max_iterations: int = 60) -> dict[int, str]:
        """
        Process a guide by having the LLM read and execute each step.
        Returns: dict mapping marker index → base64 data URI
        """
        from .guide import parse_markers

        markers = parse_markers(guide_text)
        marker_summary = "\n".join(
            f"  [{m.index}] Line {m.line}: {m.description}" for m in markers
        )

        system_prompt = f"""You are a browser automation agent. Your job is to follow the steps in a lab guide and take screenshots at marked points.

You are controlling a Playwright browser that is already authenticated to the Okta Admin Console at {self.admin_url}.

## Your tools
- navigate: Go to a URL path (e.g., '/admin/dashboard')
- click: Click elements using Playwright selectors
- fill: Type into input fields
- select_option: Select from dropdowns
- get_page_state: See current URL + all visible interactive elements (CALL THIS FIRST and after each navigation)
- get_page_text: Read page text content
- wait: Wait for time or elements
- capture_screenshot: Capture the page (only at [SCREENSHOT] markers)
- done: Signal completion

## Navigation tips for the Okta Admin Console
- The left sidebar has collapsible sections (Security, Reports, Applications, etc.)
- To navigate to a sub-page like "System Log" under "Reports":
  1. Call get_page_state to see the sidebar
  2. Click the section header to expand it (e.g., click on the Reports menu item)
  3. Call get_page_state again to see the expanded sub-items
  4. Click the specific sub-item
- If a direct URL path gives a 404, use sidebar click navigation instead
- After clicking, always verify with get_page_state that you arrived at the right page
- Dismiss popup overlays by clicking close/dismiss buttons
- Some steps in the guide refer to actions on external platforms (attack simulators, virtual desktops) — skip those steps but still capture screenshots for the admin console pages
- The guide may reference specific data (log entries, specific users, etc.) that doesn't exist in this environment. That's OK — navigate to the correct page and capture it as-is. The screenshot shows the right page/feature, even if the specific data differs.

## IMPORTANT: Always capture screenshots
When you reach a [SCREENSHOT] marker and you're on the correct page (or the closest page you can navigate to), ALWAYS call capture_screenshot. Don't skip a screenshot because the page doesn't show the exact data described. The goal is to show the right admin console page/feature.

## Screenshot markers in this guide
{marker_summary}

When you reach the point in the guide where a [SCREENSHOT: ...] marker appears, and you have completed the preceding steps, call capture_screenshot with the correct marker_index.

## Important
- Start by calling get_page_state to orient yourself
- Read the guide carefully — some steps may refer to external systems you can't access. Skip those but still capture any screenshots that show admin console pages
- If a step says "go to Security > Authentication Policies", that means: expand Security in sidebar, then click Authentication Policies
- Be persistent — if one approach fails, try another (different selector, force click, etc.)
- Call done when you've processed all sections and captured all possible screenshots"""

        messages = [
            {
                "role": "user",
                "content": f"Here is the lab guide. Follow each step you can execute in the Okta Admin Console and capture screenshots at the marked points:\n\n---\n\n{guide_text}",
            }
        ]

        # LiteLLM uses the Anthropic messages format
        litellm_kwargs = {
            "model": self.model_id,
            "messages": messages,
            "tools": BROWSER_TOOLS,
            "max_tokens": 4096,
        }

        # Add system prompt
        if os.environ.get("LITELLM_API_BASE"):
            litellm_kwargs["api_base"] = os.environ["LITELLM_API_BASE"]
            litellm_kwargs["api_key"] = os.environ.get("LITELLM_API_KEY", "")

        for iteration in range(max_iterations):
            self._log(f"iteration {iteration + 1}/{max_iterations}")

            try:
                response = self.completion(
                    **litellm_kwargs,
                    system=system_prompt,
                )
            except Exception as e:
                self._log(f"LLM error: {e}")
                break

            message = response.choices[0].message
            stop_reason = response.choices[0].finish_reason

            # Add assistant message to history
            messages.append({"role": "assistant", "content": message.content, "tool_calls": message.tool_calls})

            if message.tool_calls:
                tool_results = []
                is_done = False

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_input = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_input = {}

                    self._log(f"tool: {tool_name}({json.dumps(tool_input)[:80]})")
                    result = self._execute_tool(tool_name, tool_input)

                    if result == "__DONE__":
                        is_done = True
                        result = "Guide processing complete."

                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

                messages.extend(tool_results)

                # Update kwargs with new messages
                litellm_kwargs["messages"] = messages

                if is_done:
                    break
            else:
                # No tool calls — model is done or thinking
                if message.content:
                    self._log(f"text: {str(message.content)[:100]}...")
                break

        self._log(f"Completed: {len(self.images)}/{len(markers)} screenshots captured")
        return self.images
