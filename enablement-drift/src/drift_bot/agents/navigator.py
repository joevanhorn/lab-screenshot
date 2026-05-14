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

    # Step 5: Handle "Keep me signed in" prompt
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
        # Navigate based on breadcrumbs
        if expectation.url_hint:
            url = expectation.url_hint
            if not url.startswith("http"):
                url = f"{org_url.rstrip('/')}{url}"
            await page.goto(url, wait_until="networkidle", timeout=15000)
        elif expectation.navigation:
            await _navigate_breadcrumbs(page, expectation.navigation, org_url)

        # Wait for page to settle
        await asyncio.sleep(1)

        # Extract DOM elements
        elements = await page.evaluate(EXTRACT_ELEMENTS_JS)
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

        # Get page text
        try:
            page_text = await page.inner_text("body")
            page_text = page_text[:4000]
        except Exception:
            page_text = ""

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


async def _navigate_breadcrumbs(page, breadcrumbs: list[str], org_url: str):
    """Navigate through the Okta admin console following breadcrumbs."""
    for crumb in breadcrumbs:
        # Try clicking sidebar/menu items
        try:
            link = page.locator(f'a:has-text("{crumb}"), button:has-text("{crumb}"), [data-se]:has-text("{crumb}")').first
            if await link.count() > 0:
                await link.click()
                await page.wait_for_load_state("networkidle", timeout=10000)
                await asyncio.sleep(0.5)
                continue
        except Exception:
            pass

        # Try tab navigation
        try:
            tab = page.locator(f'[role="tab"]:has-text("{crumb}"), .tab:has-text("{crumb}")').first
            if await tab.count() > 0:
                await tab.click()
                await asyncio.sleep(0.5)
                continue
        except Exception:
            pass

        print(f"    ⚠️  Could not find navigation element: {crumb}")
