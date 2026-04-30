#!/usr/bin/env python3
"""
End-to-end test: execute sample-guide.md with Okta API + Playwright.

Set environment variables before running:
    OKTA_ORG=https://your-org.okta.com
    OKTA_API_TOKEN=your-ssws-token
    OKTA_BOT_USERNAME=bot@your-org.com
    OKTA_BOT_PASSWORD=password
    OKTA_BOT_TOTP_SECRET=BASE32SECRET  (optional, for automated MFA)
"""

import json, os, sys, shutil, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lab_screenshot.guide import parse_markers, capture_to_base64, replace_markers

OKTA_ORG = os.environ.get("OKTA_ORG", "")
API_TOKEN = os.environ.get("OKTA_API_TOKEN", "")
BOT_USERNAME = os.environ.get("OKTA_BOT_USERNAME", "")
BOT_PASSWORD = os.environ.get("OKTA_BOT_PASSWORD", "")
BOT_TOTP_SECRET = os.environ.get("OKTA_BOT_TOTP_SECRET", "")

if not all([OKTA_ORG, API_TOKEN, BOT_USERNAME, BOT_PASSWORD]):
    print("Set: OKTA_ORG, OKTA_API_TOKEN, OKTA_BOT_USERNAME, OKTA_BOT_PASSWORD")
    sys.exit(1)

OKTA_ADMIN = OKTA_ORG.replace(".okta.com", "-admin.okta.com").replace(".oktapreview.com", "-admin.oktapreview.com")
GUIDE_INPUT = Path(__file__).parent / "sample-guide.md"
GUIDE_OUTPUT = Path(__file__).parent / "sample-guide-output.md"

def okta_api(method, path, body=None):
    url = f"{OKTA_ORG}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"SSWS {API_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def login_and_get_page(pw):
    import pyotp
    body = json.dumps({"username": BOT_USERNAME, "password": BOT_PASSWORD}).encode()
    req = urllib.request.Request(f"{OKTA_ORG}/api/v1/authn", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        session_token = json.loads(resp.read().decode())["sessionToken"]

    profile = os.path.expanduser("~/.okta-lab-screenshots/test-profile")
    shutil.rmtree(profile, ignore_errors=True); os.makedirs(profile)
    ctx = pw.chromium.launch_persistent_context(user_data_dir=profile, headless=True,
        viewport={"width": 1440, "height": 900}, args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f"{OKTA_ORG}/login/sessionCookieRedirect?token={session_token}&redirectUrl={OKTA_ADMIN}/admin/dashboard",
              wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    page.goto(f"{OKTA_ADMIN}/admin/dashboard", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    if "credentials.totp" in page.content() and BOT_TOTP_SECRET:
        page.fill('input[name="credentials.totp"]', pyotp.TOTP(BOT_TOTP_SECRET).now())
        page.click('input[data-type="save"]'); page.wait_for_timeout(5000)
    if "Stay signed in" in page.inner_text("body"):
        page.click('a[data-se="stay-signed-in-btn"]'); page.wait_for_timeout(5000)
    try:
        cb = page.locator('button:has-text("Close"), [aria-label="Close"]').first
        if cb.is_visible(timeout=2000): cb.click()
    except: pass
    print(f"Authenticated: {page.url}")
    return ctx, page

def main():
    text = GUIDE_INPUT.read_text(); markers = parse_markers(text)
    print(f"{len(markers)} markers found")

    app = okta_api("POST", "/api/v1/apps", {"name": "bookmark", "label": "Screenshot-Test",
        "signOnMode": "BOOKMARK", "settings": {"app": {"requestIntegration": False, "url": "https://example.com"}}})
    app_id, app_name = app["id"], app.get("name", "bookmark")
    print(f"Created app: {app_id}")

    from playwright.sync_api import sync_playwright
    images = {}
    with sync_playwright() as p:
        ctx, page = login_and_get_page(p)
        page.goto(f"{OKTA_ADMIN}/admin/app/{app_name}/instance/{app_id}/#tab-general",
                  wait_until="networkidle", timeout=30000); page.wait_for_timeout(2000)
        for i in range(min(len(markers), 3)):
            images[i] = capture_to_base64(page)
        ctx.close()

    updated = replace_markers(text, images)
    GUIDE_OUTPUT.write_text(updated)
    print(f"Output: {GUIDE_OUTPUT} ({len(updated):,} bytes)")

    # Cleanup
    for path, method in [(f"/api/v1/apps/{app_id}/lifecycle/deactivate", "POST"), (f"/api/v1/apps/{app_id}", "DELETE")]:
        try:
            req = urllib.request.Request(f"{OKTA_ORG}{path}", method=method,
                headers={"Authorization": f"SSWS {API_TOKEN}", "Content-Type": "application/json"})
            urllib.request.urlopen(req)
        except: pass
    print("Done!")

if __name__ == "__main__":
    sys.exit(main())
