#!/usr/bin/env python3
"""
screenshot.py — Core screenshot engine using Playwright.

Takes a screenshot of an Okta Admin Console page using a persistent
browser profile (preserves session, fingerprint, avoids ITP detection).

Usage:
  # One-time: log in and save the browser profile
  python3 -m src.screenshot setup --org https://your-org-admin.okta.com

  # Take a single screenshot
  python3 -m src.screenshot capture \
    --org https://your-org-admin.okta.com \
    --path /admin/oauth2/as \
    --output screenshot.png

  # Take a screenshot with a CSS selector wait
  python3 -m src.screenshot capture \
    --org https://your-org-admin.okta.com \
    --path /admin/oauth2/as \
    --wait-for "table" \
    --output auth-servers.png
"""

import argparse
import base64
import getpass
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


# Default browser profile directory
DEFAULT_PROFILE_DIR = os.path.expanduser("~/.okta-lab-screenshots/browser-profile")


def get_browser_profile_dir(profile_dir: Optional[str] = None) -> str:
    """Get or create the browser profile directory."""
    path = profile_dir or DEFAULT_PROFILE_DIR
    os.makedirs(path, exist_ok=True)
    return path


def setup_browser_profile(org_url: str, profile_dir: Optional[str] = None):
    """
    Launch a visible browser for the user to log in manually.
    The browser profile is saved for reuse by the screenshot engine.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright required. Install with:", file=sys.stderr)
        print("  pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    profile = get_browser_profile_dir(profile_dir)
    admin_url = org_url.rstrip("/")
    if not admin_url.endswith("-admin.okta.com") and "-admin" not in admin_url:
        # Convert org URL to admin URL
        admin_url = admin_url.replace(".okta.com", "-admin.okta.com")

    print(f"Opening browser with persistent profile at: {profile}")
    print(f"Navigate to: {admin_url}")
    print(f"Log in with your Okta admin credentials.")
    print(f"Once logged in, close the browser window to save the session.")
    print()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto(admin_url)

        # Wait for user to close the browser
        try:
            page.wait_for_event("close", timeout=600000)  # 10 min timeout
        except Exception:
            pass

        context.close()

    print(f"\nSession saved to: {profile}")
    print("You can now use 'capture' to take screenshots.")


def login_browser_profile(
    org_url: str,
    username: str,
    password: str,
    totp_secret: Optional[str] = None,
    profile_dir: Optional[str] = None,
):
    """
    Authenticate to Okta headlessly using the authn API + Playwright.
    No GUI required.

    Flow:
      1. POST /api/v1/authn → get sessionToken (handles MFA via API if
         totp_secret is provided, or via authn API push/TOTP prompt)
      2. Navigate Playwright to /login/sessionCookieRedirect?token=...
      3. Follow the admin console OAuth/PKCE flow
      4. Handle in-browser MFA challenge (TOTP) if the OAuth layer requires it
      5. Handle "Keep me signed in" prompt
      6. Persistent browser profile is saved with session cookies

    Args:
        org_url: Okta org URL (e.g., https://your-org.okta.com)
        username: Okta username (email)
        password: Okta password
        totp_secret: TOTP shared secret for automated MFA (base32 string)
        profile_dir: Browser profile directory
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright required.", file=sys.stderr)
        sys.exit(1)

    org = org_url.rstrip("/")
    admin_url = org.replace(".okta.com", "-admin.okta.com") if "-admin" not in org else org
    base_org = org.replace("-admin.okta.com", ".okta.com") if "-admin" in org else org

    # --- Step 1: Get session token via authn API ---
    print(f"Authenticating as {username}...", file=sys.stderr)
    authn_url = f"{base_org}/api/v1/authn"
    body = json.dumps({"username": username, "password": password}).encode()

    req = urllib.request.Request(authn_url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err = e.read().decode() if e.fp else ""
        if e.code == 401:
            print("ERROR: Invalid username or password.", file=sys.stderr)
        elif e.code == 403:
            print(f"ERROR: Authentication blocked (403).", file=sys.stderr)
        else:
            print(f"ERROR: authn API returned {e.code}: {err}", file=sys.stderr)
        sys.exit(1)

    status = data.get("status")
    session_token = data.get("sessionToken")

    if status == "MFA_REQUIRED":
        print("MFA required at authn level.", file=sys.stderr)
        factors = data.get("_embedded", {}).get("factors", [])
        state_token = data["stateToken"]

        # Try TOTP first (if secret provided), then push, then prompt
        totp_factor = next((f for f in factors if f.get("factorType") == "token:software:totp"), None)
        push_factor = next((f for f in factors if f.get("factorType") == "push"), None)

        if totp_factor and totp_secret:
            try:
                import pyotp
                code = pyotp.TOTP(totp_secret).now()
            except ImportError:
                print("ERROR: pyotp required for automated TOTP. pip install pyotp", file=sys.stderr)
                sys.exit(1)
            print(f"  Verifying TOTP...", file=sys.stderr)
            verify_url = totp_factor["_links"]["verify"]["href"]
            vbody = json.dumps({"stateToken": state_token, "passCode": code}).encode()
            vreq = urllib.request.Request(verify_url, data=vbody, method="POST")
            vreq.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(vreq) as vresp:
                vdata = json.loads(vresp.read().decode())
            session_token = vdata.get("sessionToken")

        elif push_factor:
            print("  Sending Okta Verify push...", file=sys.stderr)
            verify_url = push_factor["_links"]["verify"]["href"]
            for attempt in range(30):
                vbody = json.dumps({"stateToken": state_token}).encode()
                vreq = urllib.request.Request(verify_url, data=vbody, method="POST")
                vreq.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(vreq) as vresp:
                    vdata = json.loads(vresp.read().decode())
                if vdata.get("status") == "SUCCESS":
                    session_token = vdata.get("sessionToken")
                    print("  Push approved!", file=sys.stderr)
                    break
                elif vdata.get("factorResult") == "WAITING":
                    print(f"  Waiting... ({attempt+1}/30)", file=sys.stderr)
                    time.sleep(2)
                    state_token = vdata.get("stateToken", state_token)
                else:
                    print(f"ERROR: Push status: {vdata.get('factorResult')}", file=sys.stderr)
                    sys.exit(1)
            else:
                print("ERROR: Push timed out.", file=sys.stderr)
                sys.exit(1)

        elif totp_factor:
            code = input("Enter MFA code: ").strip()
            verify_url = totp_factor["_links"]["verify"]["href"]
            vbody = json.dumps({"stateToken": state_token, "passCode": code}).encode()
            vreq = urllib.request.Request(verify_url, data=vbody, method="POST")
            vreq.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(vreq) as vresp:
                vdata = json.loads(vresp.read().decode())
            session_token = vdata.get("sessionToken")

        else:
            factor_types = [f.get("factorType") for f in factors]
            print(f"ERROR: No supported MFA factor. Available: {factor_types}", file=sys.stderr)
            sys.exit(1)

    elif status != "SUCCESS":
        print(f"ERROR: Unexpected authn status: {status}", file=sys.stderr)
        sys.exit(1)

    if not session_token:
        print("ERROR: No session token received.", file=sys.stderr)
        sys.exit(1)

    print("  Session token acquired.", file=sys.stderr)

    # --- Step 2: Establish browser session ---
    profile = get_browser_profile_dir(profile_dir)
    cookie_url = f"{base_org}/login/sessionCookieRedirect?token={session_token}&redirectUrl={admin_url}/admin/dashboard"

    print("Establishing browser session...", file=sys.stderr)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=True,
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Cookie redirect — establishes Okta session cookie
        page.goto(cookie_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Navigate to admin dashboard — triggers OAuth/PKCE SSO flow
        page.goto(f"{admin_url}/admin/dashboard", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Handle in-browser MFA (OAuth layer may re-challenge)
        for attempt in range(3):
            page_text = page.inner_text("body")

            # MFA TOTP prompt in the browser
            if "Enter a code" in page_text or "credentials.totp" in page.content():
                if not totp_secret:
                    print("ERROR: Browser MFA prompt but no --totp-secret provided.", file=sys.stderr)
                    context.close()
                    sys.exit(1)
                try:
                    import pyotp
                    code = pyotp.TOTP(totp_secret).now()
                except ImportError:
                    print("ERROR: pyotp required. pip install pyotp", file=sys.stderr)
                    context.close()
                    sys.exit(1)
                print(f"  Browser MFA: entering TOTP...", file=sys.stderr)
                page.fill('input[name="credentials.totp"]', code)
                page.click('input[data-type="save"]')
                page.wait_for_timeout(5000)
                continue

            # "Keep me signed in" prompt
            if "Stay signed in" in page_text or "Keep me signed in" in page_text:
                print("  Clicking 'Stay signed in'...", file=sys.stderr)
                try:
                    page.click('a[data-se="stay-signed-in-btn"]', timeout=5000)
                    page.wait_for_timeout(8000)
                except Exception:
                    pass
                continue

            # Check if we made it
            if "/admin/" in page.url:
                break

        final_url = page.url
        if "/admin/" not in final_url:
            print(f"WARNING: Did not reach admin console: {final_url}", file=sys.stderr)
            context.close()
            sys.exit(1)

        print(f"Authenticated! URL: {final_url}", file=sys.stderr)
        print(f"Session saved to: {profile}", file=sys.stderr)
        context.close()

    print("You can now use 'capture' to take authenticated screenshots.", file=sys.stderr)


def capture_screenshot(
    org_url: str,
    path: str,
    output: Optional[str] = None,
    wait_for: Optional[str] = None,
    wait_timeout: int = 10000,
    delay: int = 2000,
    viewport_width: int = 1440,
    viewport_height: int = 900,
    full_page: bool = False,
    clip: Optional[dict] = None,
    profile_dir: Optional[str] = None,
    headless: bool = True,
) -> bytes:
    """
    Capture a screenshot of an Okta Admin Console page.

    Args:
        org_url: Okta org URL (e.g., https://your-org.okta.com)
        path: URL path to navigate to (e.g., /admin/oauth2/as)
        output: File path to save the screenshot (optional)
        wait_for: CSS selector to wait for before capturing
        wait_timeout: Timeout for wait_for in ms
        delay: Additional delay after page load in ms
        viewport_width: Browser viewport width
        viewport_height: Browser viewport height
        full_page: Capture full page scroll
        clip: Clip region {x, y, width, height}
        profile_dir: Browser profile directory
        headless: Run headless (default True)

    Returns:
        Screenshot as PNG bytes
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright required.", file=sys.stderr)
        sys.exit(1)

    profile = get_browser_profile_dir(profile_dir)

    # Build the full URL
    admin_url = org_url.rstrip("/")
    if not admin_url.endswith("-admin.okta.com") and "-admin" not in admin_url:
        admin_url = admin_url.replace(".okta.com", "-admin.okta.com")

    full_url = f"{admin_url}{path}" if path.startswith("/") else f"{admin_url}/{path}"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=headless,
            viewport={"width": viewport_width, "height": viewport_height},
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else context.new_page()

        try:
            print(f"Navigating to: {full_url}", file=sys.stderr)
            page.goto(full_url, wait_until="networkidle", timeout=30000)

            # Check if we got redirected to login
            if "/login" in page.url or "/signin" in page.url:
                print("WARNING: Session expired — redirected to login page.", file=sys.stderr)
                print("Run 'setup' again to re-authenticate.", file=sys.stderr)
                context.close()
                sys.exit(1)

            # Wait for specific element if requested
            if wait_for:
                print(f"Waiting for: {wait_for}", file=sys.stderr)
                try:
                    page.wait_for_selector(wait_for, timeout=wait_timeout)
                except Exception:
                    print(f"WARNING: Element '{wait_for}' not found within {wait_timeout}ms", file=sys.stderr)

            # Additional delay for animations/rendering
            if delay > 0:
                page.wait_for_timeout(delay)

            # Capture screenshot
            screenshot_args = {"type": "png"}
            if full_page:
                screenshot_args["full_page"] = True
            if clip:
                screenshot_args["clip"] = clip

            png_bytes = page.screenshot(**screenshot_args)

        finally:
            context.close()

    # Save to file if output specified
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png_bytes)
        print(f"Screenshot saved: {output} ({len(png_bytes)} bytes)", file=sys.stderr)

    return png_bytes


def screenshot_to_base64(png_bytes: bytes) -> str:
    """Convert PNG bytes to a base64 data URI for inline markdown."""
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Okta Admin Console screenshot tool")
    subparsers = parser.add_subparsers(dest="command")

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Log in and save browser profile")
    setup_parser.add_argument("--org", required=True, help="Okta org URL")
    setup_parser.add_argument("--profile-dir", help="Browser profile directory")

    # Login command (headless auth via API)
    login_parser = subparsers.add_parser("login", help="Authenticate headlessly (no GUI required)")
    login_parser.add_argument("--org", required=True, help="Okta org URL")
    login_parser.add_argument("--username", help="Okta username (prompts if omitted)")
    login_parser.add_argument("--password", help="Okta password (prompts if omitted)")
    login_parser.add_argument("--totp-secret", help="TOTP shared secret for automated MFA (base32)")
    login_parser.add_argument("--profile-dir", help="Browser profile directory")

    # Capture command
    capture_parser = subparsers.add_parser("capture", help="Take a screenshot")
    capture_parser.add_argument("--org", required=True, help="Okta org URL")
    capture_parser.add_argument("--path", required=True, help="URL path (e.g., /admin/oauth2/as)")
    capture_parser.add_argument("--output", help="Output file path")
    capture_parser.add_argument("--wait-for", help="CSS selector to wait for")
    capture_parser.add_argument("--wait-timeout", type=int, default=10000, help="Wait timeout in ms")
    capture_parser.add_argument("--delay", type=int, default=2000, help="Delay after load in ms")
    capture_parser.add_argument("--width", type=int, default=1440, help="Viewport width")
    capture_parser.add_argument("--height", type=int, default=900, help="Viewport height")
    capture_parser.add_argument("--full-page", action="store_true", help="Capture full page")
    capture_parser.add_argument("--base64", action="store_true", help="Output base64 data URI")
    capture_parser.add_argument("--profile-dir", help="Browser profile directory")
    capture_parser.add_argument("--visible", action="store_true", help="Show browser")

    args = parser.parse_args()

    if args.command == "setup":
        setup_browser_profile(args.org, args.profile_dir)

    elif args.command == "login":
        username = args.username or input("Okta username: ").strip()
        password = args.password or getpass.getpass("Okta password: ")
        login_browser_profile(args.org, username, password, args.totp_secret, args.profile_dir)

    elif args.command == "capture":
        png_bytes = capture_screenshot(
            org_url=args.org,
            path=args.path,
            output=args.output,
            wait_for=args.wait_for,
            wait_timeout=args.wait_timeout,
            delay=args.delay,
            viewport_width=args.width,
            viewport_height=args.height,
            full_page=args.full_page,
            profile_dir=args.profile_dir,
            headless=not args.visible,
        )

        if args.base64:
            print(screenshot_to_base64(png_bytes))
        elif not args.output:
            # Write raw PNG to stdout
            sys.stdout.buffer.write(png_bytes)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
