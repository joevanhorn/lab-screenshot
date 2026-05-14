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

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1080},
        )
        page = await context.new_page()

        # Authenticate
        try:
            await _authenticate(page, org_url, username, password, totp_secret)
            print(f"  ✅ Authenticated to {org_url}")
        except Exception as e:
            print(f"  ❌ Authentication failed: {e}")
            doc_capture.completed_at = datetime.utcnow()
            return doc_capture

        # Capture each step
        for exp in expectations.expectations:
            print(f"  📸 Step {exp.step_id}: {exp.description[:60]}...")
            state = await _capture_step(page, exp, org_url, screenshots_dir)
            doc_capture.captures.append(state)

        doc_capture.completed_at = datetime.utcnow()
        await browser.close()

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


async def _capture_step(page, expectation, org_url: str, screenshots_dir: Path) -> CapturedState:
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
        # Final wait for SPA rendering
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
    "access certifications": "/admin/access-certification",
    "access certification": "/admin/access-certification",
    "campaigns": "/admin/access-certification",
    "entitlement management": "/admin/entitlement-management",
    "entitlements": "/admin/entitlement-management",
    "bundles": "/admin/entitlement-management/bundles",
    "governance labels": "/admin/identity-governance/governance-label",
    "labels": "/admin/identity-governance/governance-label",
    "access requests": "/admin/access-requests",
    "request conditions": "/admin/access-requests",
    "security": "/admin/access/authentication",
    "authentication policies": "/admin/access/authentication",
    "network zones": "/admin/access/networks",
    "system log": "/report/system_log_2",
    "reports": "/report/system_log_2",
    "settings": "/admin/settings/account",
    "api tokens": "/admin/access/api/tokens",
}


async def _navigate_breadcrumbs(page, breadcrumbs: list[str], org_url: str):
    """
    Navigate through the Okta admin console following breadcrumbs.

    Strategy (matching the lab-screenshot bot):
    1. Try direct URL mapping first (most reliable)
    2. Try clicking sidebar section to expand, then sub-item
    3. Try tab/link click for in-page navigation
    """
    base_url = org_url.rstrip("/")

    for i, crumb in enumerate(breadcrumbs):
        crumb_lower = crumb.lower().strip()

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
