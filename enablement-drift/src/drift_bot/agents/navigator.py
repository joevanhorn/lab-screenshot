"""
Navigator Agent — drives Playwright through Okta admin console and captures state.

Reuses patterns from lab-screenshot bot (browser_agent.py, app.py).

Input: DocExpectations + Okta credentials
Output: DocCapture with screenshots and DOM state at each step
"""

import asyncio
import base64
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..models.expectations import DocExpectations
from ..models.capture import DocCapture, CapturedState, CapturedLabel
from ..config import OKTA_ORG_URL, OKTA_USERNAME, OKTA_PASSWORD, OKTA_TOTP_SECRET, RUNS_DIR


# JavaScript to extract all visible interactive elements from the page
# Adapted from lab-screenshot/lab_screenshot/browser_agent.py:233-268
EXTRACT_ELEMENTS_JS = """
() => {
    const elements = document.querySelectorAll(
        'a, button, input, select, textarea, h1, h2, h3, h4, h5, h6, ' +
        'label, [role], [data-se], nav a, .tab, [aria-label], td, th, ' +
        'span.link-button, div[role="tab"], div[role="button"], ' +
        'li[role="menuitem"], li[role="option"]'
    );
    const results = [];
    const seen = new Set();
    for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (rect.top > window.innerHeight || rect.bottom < 0) continue;

        const text = (el.textContent || '').trim().substring(0, 200);
        if (!text || seen.has(text)) continue;
        seen.add(text);

        results.push({
            text: text,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            selector: el.getAttribute('data-se') || el.id || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            href: el.getAttribute('href') || '',
            boundingBox: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            }
        });

        if (results.length >= 200) break;
    }
    return results;
}
"""


async def capture(
    expectations: DocExpectations,
    org_url: str = "",
    username: str = "",
    password: str = "",
    totp_secret: str = "",
    run_id: str = "",
    headless: bool = True,
) -> DocCapture:
    """
    Navigate through each expected step and capture the actual UI state.
    """
    from playwright.async_api import async_playwright

    org_url = org_url or OKTA_ORG_URL
    username = username or OKTA_USERNAME
    password = password or OKTA_PASSWORD
    totp_secret = totp_secret or OKTA_TOTP_SECRET
    run_id = run_id or str(uuid.uuid4())[:8]

    # Setup run directory
    run_dir = RUNS_DIR / run_id
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    doc_capture = DocCapture(
        doc_source=expectations.doc_source,
        run_id=run_id,
        org_url=org_url,
        started_at=datetime.utcnow(),
    )

    # Synced log for the recording viewer
    log_entries: list[dict] = []
    video_start_time = time.time()

    def log(message: str, level: str = "info"):
        """Add a timestamped log entry synced to video."""
        t = time.time() - video_start_time
        log_entries.append({"t": round(t, 1), "level": level, "message": message})
        print(f"  [{level:5s}] {message}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Video recording dir
        video_dir = run_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)

        context = await browser.new_context(
            viewport={"width": 1440, "height": 1080},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1440, "height": 1080},
        )
        page = await context.new_page()
        video_start_time = time.time()

        # Authenticate
        try:
            log("Authenticating to Okta admin console...", "agent")
            await _authenticate(page, org_url, username, password, totp_secret)
            log(f"✅ Authenticated to {org_url}", "agent")
        except Exception as e:
            log(f"❌ Authentication failed: {e}", "error")
            doc_capture.completed_at = datetime.utcnow()
            return doc_capture

        # Capture each step
        for exp in expectations.expectations:
            log(f"📖 Step {exp.step_id}: {exp.description}", "agent")
            if exp.navigation:
                log(f"🧭 Navigating: {' > '.join(exp.navigation)}", "agent")

            state = await _capture_step(page, exp, org_url, screenshots_dir, log)
            doc_capture.captures.append(state)

            if state.error:
                log(f"❌ Capture failed: {state.error}", "error")
            else:
                label_count = len(state.accessible_labels)
                log(f"📸 Captured {label_count} UI elements at {state.url}", "agent")

                # Log which expected labels were found/missing
                found_texts = {l.text.lower() for l in state.accessible_labels}
                for label in exp.labels:
                    if label.text.lower() in found_texts:
                        log(f"  ✅ Found: \"{label.text}\"", "info")
                    else:
                        log(f"  ❌ Missing: \"{label.text}\"", "error")

        doc_capture.completed_at = datetime.utcnow()

        # Close context first to flush video, then browser
        video_path = await page.video.path() if page.video else None
        await context.close()
        await browser.close()

        if video_path:
            log(f"🎬 Video saved: {video_path}", "agent")

        # Generate the synced recording viewer
        _generate_viewer(run_dir, video_dir, log_entries)

    return doc_capture


async def _authenticate(page, org_url: str, username: str, password: str, totp_secret: str = ""):
    """Authenticate to Okta admin console via browser-based OIE login."""

    # Navigate directly to the admin console — this triggers the OIDC login flow
    await page.goto(org_url, wait_until="networkidle", timeout=30000)
    await asyncio.sleep(3)

    # Debug: screenshot the login page
    await page.screenshot(path="/tmp/drift-auth-step1.png")
    print(f"    Auth page URL: {page.url}")

    # Step 1: Enter username (try multiple selectors)
    username_field = page.locator('input[name="identifier"], input[name="username"], input[type="text"]:visible')
    if await username_field.count() > 0:
        await username_field.fill(username)
        await page.locator('input[type="submit"]').first.click()
        await asyncio.sleep(3)

    # Step 2: Select Password authenticator (OIE shows method selection)
    for sel in ['div[data-se="okta_password"]', 'a:has-text("Select")']:
        el = page.locator(sel)
        if sel == 'a:has-text("Select")':
            # Password is usually the last "Select" link
            if await el.count() > 1:
                await el.last.click()
                await asyncio.sleep(2)
                break
        elif await el.count() > 0:
            await el.first.click()
            await asyncio.sleep(2)
            break

    # Step 3: Enter password
    pwd_field = page.locator('input[type="password"]')
    if await pwd_field.count() > 0:
        await pwd_field.fill(password)
        await page.locator('input[type="submit"]').first.click()
        await asyncio.sleep(3)

    # Step 4: Handle TOTP MFA
    if totp_secret:
        import pyotp
        totp_input = page.locator('input[type="text"]:visible')
        if await totp_input.count() > 0:
            code = pyotp.TOTP(totp_secret).now()
            await totp_input.first.fill(code)
            await page.locator('input[type="submit"]').first.click()
            await asyncio.sleep(5)

    # Step 5: Dismiss any Pendo/overlay popups
    await _dismiss_popups(page)

    # Step 6: Handle "Keep me signed in" prompt
    await asyncio.sleep(2)
    stay_signed_in = page.locator('input[value="Stay signed in"], button:has-text("Stay signed in"), a:has-text("Stay signed in")')
    if await stay_signed_in.count() > 0:
        await stay_signed_in.first.click()
        await asyncio.sleep(3)

    # Also handle "Don't stay signed in" as a fallback
    dont_stay = page.locator('input[value="Don\'t stay signed in"], button:has-text("Don\'t stay signed in"), a:has-text("Don\'t stay signed in")')
    if await dont_stay.count() > 0:
        await dont_stay.first.click()
        await asyncio.sleep(3)

    # Wait for admin console to load
    await page.wait_for_load_state("networkidle", timeout=30000)
    await asyncio.sleep(3)


def _generate_viewer(run_dir: Path, video_dir: Path, log_entries: list[dict]):
    """Generate the self-contained HTML viewer with synced video + log."""
    import json as json_mod

    webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_size, reverse=True)
    primary = webms[0].name if webms else ""

    log_json = json_mod.dumps(log_entries, ensure_ascii=False)

    viewer_html = _VIDEO_VIEWER_HTML.replace("__PRIMARY_VIDEO__", f"video/{primary}").replace("__LOG_JSON__", log_json)

    viewer_path = run_dir / "recording-viewer.html"
    viewer_path.write_text(viewer_html, encoding="utf-8")

    # Also write the JSONL
    jsonl_path = run_dir / "recording-log.jsonl"
    with jsonl_path.open("w") as f:
        for e in log_entries:
            f.write(json_mod.dumps(e) + "\n")

    print(f"  📺 Viewer: {viewer_path}")


async def _capture_step(page, expectation, org_url: str, screenshots_dir: Path, log=None) -> CapturedState:
    """Navigate to a step and capture DOM state + screenshot."""
    try:
        # Dismiss any popups before navigation
        await _dismiss_popups(page)

        # Navigate based on breadcrumbs
        if expectation.url_hint:
            url = expectation.url_hint
            if not url.startswith("http"):
                url = f"{org_url.rstrip('/')}{url}"
            await page.goto(url, wait_until="networkidle", timeout=15000)
        elif expectation.navigation:
            await _navigate_breadcrumbs(page, expectation.navigation, org_url)

        # Wait for page to settle — SPA pages need extra time to hydrate
        await asyncio.sleep(3)
        # Dismiss popups that may have appeared after navigation
        await _dismiss_popups(page)
        await asyncio.sleep(1)
        # Wait for any loading spinners to disappear
        try:
            await page.wait_for_selector('.loading, .spinner, [class*="loading"]', state='hidden', timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(1)

        # Execute in-page actions described in the step (click tabs, expand sections, etc.)
        await _execute_step_actions(page, expectation)
        await asyncio.sleep(2)

        # Extract DOM elements from main frame AND any iframes
        elements = await page.evaluate(EXTRACT_ELEMENTS_JS) or []

        # Also extract from iframes (governance pages render inside iframes)
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if frame.url and 'about:blank' not in frame.url:
                try:
                    frame_elements = await frame.evaluate(EXTRACT_ELEMENTS_JS)
                    if frame_elements:
                        elements.extend(frame_elements)
                except Exception:
                    pass
        labels = [
            CapturedLabel(
                text=el.get("text", ""),
                selector=el.get("selector", ""),
                tag=el.get("tag", ""),
                role=el.get("role", ""),
                bounding_box=el.get("boundingBox"),
                attributes={
                    k: v for k, v in el.items()
                    if k not in ("text", "selector", "tag", "role", "boundingBox") and v
                },
            )
            for el in (elements or [])
        ]

        # Get page text from main frame and iframes
        page_text = ""
        try:
            page_text = await page.inner_text("body")
        except Exception:
            pass
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if frame.url and 'about:blank' not in frame.url:
                try:
                    frame_text = await frame.inner_text("body")
                    if frame_text:
                        page_text += "\n" + frame_text
                except Exception:
                    pass
        page_text = page_text[:8000]

        # Screenshot
        screenshot_path = str(screenshots_dir / f"{expectation.step_id}.png")
        await page.screenshot(path=screenshot_path, full_page=False)

        # Base64 for LLM vision
        with open(screenshot_path, "rb") as f:
            screenshot_b64 = base64.b64encode(f.read()).decode()

        return CapturedState(
            step_id=expectation.step_id,
            url=page.url,
            page_title=await page.title(),
            screenshot_path=screenshot_path,
            screenshot_base64=screenshot_b64,
            accessible_labels=labels,
            page_text=page_text,
            navigation_breadcrumb=expectation.navigation,
            capture_timestamp=datetime.utcnow(),
        )
    except Exception as e:
        return CapturedState(
            step_id=expectation.step_id,
            error=str(e),
            capture_timestamp=datetime.utcnow(),
        )


async def _dismiss_popups(page):
    """Dismiss Pendo guides, cookie banners, and other overlay popups."""
    try:
        # Pendo guide/tooltip
        await page.evaluate("""() => {
            const pendo = document.getElementById('pendo-base');
            if (pendo) pendo.remove();
            // Also remove any pendo overlay
            const overlay = document.querySelector('[id*="pendo"]');
            if (overlay) overlay.remove();
        }""")
    except Exception:
        pass

    try:
        # Cookie consent / generic dismiss buttons / Pendo buttons
        for sel in ['button:has-text("Dismiss")', 'a:has-text("Dismiss")',
                    'button:has-text("Got it")', 'button:has-text("Close")',
                    '[aria-label="Close"]', '[aria-label="close"]',
                    'button:has-text("Not now")', '.pendo-close-guide-x',
                    'button:has-text("Skip")', 'button:has-text("Maybe later")',
                    '[data-testid="close-button"]']:
            try:
                el = page.locator(sel)
                if await el.count() > 0:
                    await el.first.click(force=True, timeout=2000)
                    await asyncio.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass


# Known direct URL mappings for Okta admin console pages
# Adapted from lab-screenshot bot's Okta UI cheat sheet
ADMIN_URL_MAP = {
    "dashboard": "/admin/dashboard",
    "users": "/admin/users",
    "people": "/admin/users",
    "groups": "/admin/groups",
    "applications": "/admin/apps/active",
    "identity governance": "/admin/identity-governance",
    "access certifications": "/admin/governance/campaigns/active",
    "access certification": "/admin/governance/campaigns/active",
    "campaigns": "/admin/governance/campaigns/active",
    "entitlement management": "/admin/governance/entitlement-management",
    "entitlements": "/admin/governance/entitlement-management",
    "bundles": "/admin/governance/entitlement-management/bundles",
    "governance labels": "/admin/governance/governance-labels",
    "labels": "/admin/governance/governance-labels",
    "access requests": "/admin/governance/access-requests",
    "request conditions": "/admin/governance/access-requests",
    "security": "/admin/access/authentication",
    "authentication policies": "/admin/access/authentication",
    "network zones": "/admin/access/networks",
    "system log": "/report/system_log_2",
    "reports": "/report/system_log_2",
    "settings": "/admin/settings/account",
    "api tokens": "/admin/access/api/tokens",
}


async def _execute_step_actions(page, expectation):
    """
    Execute in-page actions described in the step — click tabs, buttons, expand sections.
    Parses the step description and expected labels to find clickable elements.
    Works across main frame and iframes.
    """
    desc = expectation.description.lower()
    labels_to_click = []

    # Extract action targets from expected labels
    for label in expectation.labels:
        role = label.semantic_role
        text = label.text
        # Click tabs, buttons, and menu items — these are interactive
        if role in ("tab", "button", "menu_item", "link"):
            labels_to_click.append(text)

    # Also parse "click on X" patterns from the description
    import re
    click_patterns = re.findall(r'click (?:on |the )?(?:\*\*)?([^*\n.]+?)(?:\*\*)?(?:\s+(?:tab|button|link))?', desc, re.IGNORECASE)
    for match in click_patterns:
        clean = match.strip().rstrip(' to')
        if clean and len(clean) > 2:
            labels_to_click.append(clean)

    if not labels_to_click:
        return

    # Try clicking each target in both main frame and iframes
    for target in labels_to_click:
        clicked = False
        all_frames = [page] + [f for f in page.frames if f != page.main_frame and f.url and 'about:blank' not in f.url]

        for frame in all_frames:
            for sel in [
                f'[role="tab"]:has-text("{target}")',
                f'button:has-text("{target}")',
                f'a:has-text("{target}")',
                f'text="{target}"',
            ]:
                try:
                    el = frame.locator(sel).first
                    if await el.count() > 0:
                        await el.click(force=True, timeout=3000)
                        await asyncio.sleep(1.5)
                        print(f"    ✓ Clicked: \"{target}\"")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            # Don't warn for section headers or labels we just want to verify exist
            pass


async def _navigate_breadcrumbs(page, breadcrumbs: list[str], org_url: str):
    """
    Navigate through the Okta admin console following breadcrumbs.

    Strategy (matching the lab-screenshot bot):
    1. Try direct URL mapping first (most reliable)
    2. Try clicking sidebar section to expand, then sub-item
    3. Try tab/link click for in-page navigation
    """
    base_url = org_url.rstrip("/")

    # Try the LAST breadcrumb for direct URL first (most specific)
    last_crumb = breadcrumbs[-1].lower().strip() if breadcrumbs else ""
    if last_crumb in ADMIN_URL_MAP:
        url = f"{base_url}{ADMIN_URL_MAP[last_crumb]}"
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
            await asyncio.sleep(2)
            title = await page.title()
            if "not found" not in title.lower():
                return  # Successfully navigated via URL
        except Exception:
            pass

    for i, crumb in enumerate(breadcrumbs):
        crumb_lower = crumb.lower().strip()

        # Strategy 0: Direct URL navigation
        if crumb_lower in ADMIN_URL_MAP:
            url = f"{base_url}{ADMIN_URL_MAP[crumb_lower]}"
            try:
                await page.goto(url, wait_until="networkidle", timeout=15000)
                await asyncio.sleep(2)
                # Check if it's a 404
                title = await page.title()
                if "not found" not in title.lower():
                    continue
                # 404 — fall through to click-based navigation
            except Exception:
                pass

        # Strategy 1: Dismiss popups and click sidebar/page elements
        await _dismiss_popups(page)

        # Try text= selector with force click (bypasses Pendo overlays)
        try:
            el = page.locator(f'text="{crumb}"').first
            if await el.count() > 0:
                await el.click(force=True, timeout=5000)
                await asyncio.sleep(2)
                continue
        except Exception:
            pass

        # Strategy 2: Try various Playwright selectors with force
        for sel in [
            f'a:has-text("{crumb}")',
            f'button:has-text("{crumb}")',
            f'[role="tab"]:has-text("{crumb}")',
            f'[data-se]:has-text("{crumb}")',
            f'.tab:has-text("{crumb}")',
        ]:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click(force=True, timeout=5000)
                    await page.wait_for_load_state("networkidle", timeout=8000)
                    await asyncio.sleep(1)
                    break
            except Exception:
                continue
        else:
            # Strategy 3: Try URL guess from breadcrumb text
            slug = crumb_lower.replace(" ", "-")
            guess_urls = [
                f"{base_url}/admin/{slug}",
                f"{base_url}/admin/identity-governance/{slug}",
            ]
            navigated = False
            for guess_url in guess_urls:
                try:
                    resp = await page.goto(guess_url, wait_until="networkidle", timeout=8000)
                    if resp and resp.status < 400:
                        await asyncio.sleep(1)
                        navigated = True
                        break
                except Exception:
                    continue
            if not navigated:
                print(f"    ⚠️  Could not navigate to: {crumb}")


_VIDEO_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Drift Detection — Session Recording</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; display: flex; flex-direction: column; }
  header { padding: 10px 16px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 14px; font-weight: 600; }
  header .meta { font-size: 12px; color: #94a3b8; }
  main { flex: 1; display: flex; min-height: 0; }
  .video-pane { flex: 1; display: flex; align-items: center; justify-content: center; background: #000; padding: 12px; min-width: 0; }
  video { max-width: 100%; max-height: 100%; background: #000; }
  .log-pane { width: 480px; min-width: 360px; max-width: 50vw; resize: horizontal; overflow: auto; background: #1e293b; border-left: 1px solid #334155; }
  .log-entry { padding: 6px 12px; border-bottom: 1px solid #334155; cursor: pointer; font-size: 12px; line-height: 1.5; transition: background 80ms; }
  .log-entry:hover { background: #334155; }
  .log-entry.active { background: #2563eb; color: #fff; }
  .log-entry.active .t { color: #bfdbfe; }
  .log-entry .t { display: inline-block; width: 56px; color: #64748b; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  .log-entry .lvl { display: inline-block; width: 56px; color: #94a3b8; font-size: 10px; text-transform: uppercase; }
  .log-entry .lvl.error { color: #f87171; }
  .log-entry .lvl.agent { color: #34d399; }
  .log-entry .msg { color: inherit; }
</style>
</head>
<body>
<header>
  <h1>🔎 Drift Detection — Session Recording</h1>
  <span class="meta">Click any log row to jump the video to that moment</span>
</header>
<main>
  <div class="video-pane">
    <video id="player" controls></video>
  </div>
  <div class="log-pane" id="log-pane"></div>
</main>
<script>
const VIDEO_SRC = "__PRIMARY_VIDEO__";
const LOG = __LOG_JSON__;

const player = document.getElementById('player');
const pane = document.getElementById('log-pane');

player.src = VIDEO_SRC;

function fmtTime(s) {
  if (s == null) return '--:--';
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
}

LOG.forEach((e, i) => {
  const row = document.createElement('div');
  row.className = 'log-entry';
  row.dataset.idx = i;
  row.dataset.t = e.t;
  row.innerHTML = '<span class="t">' + fmtTime(e.t) + '</span>'
    + '<span class="lvl ' + (e.level || 'info') + '">' + (e.level || 'info') + '</span>'
    + '<span class="msg"></span>';
  row.querySelector('.msg').textContent = e.message;
  row.addEventListener('click', () => {
    if (player.src) { player.currentTime = e.t; player.play(); }
  });
  pane.appendChild(row);
});

player.addEventListener('timeupdate', () => {
  const t = player.currentTime;
  const rows = pane.querySelectorAll('.log-entry');
  let active = -1;
  for (let i = 0; i < rows.length; i++) {
    if (parseFloat(rows[i].dataset.t) <= t) active = i; else break;
  }
  rows.forEach((r, i) => r.classList.toggle('active', i === active));
  if (active >= 0) {
    const row = rows[active];
    const r = row.getBoundingClientRect();
    const p = pane.getBoundingClientRect();
    if (r.top < p.top || r.bottom > p.bottom) {
      row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }
});
</script>
</body>
</html>"""
