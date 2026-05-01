#!/usr/bin/env python3
"""
lab-screenshot — CLI for automated Okta Admin Console screenshots.

Commands:
    login   Authenticate headlessly (saves browser session)
    capture Take a single screenshot of an admin page
    run     Process a guide: login, navigate, capture at markers, output markdown
    check   Parse a guide and list markers (dry run, no screenshots)

Usage:
    lab-screenshot login --org https://your-org.okta.com --username user@okta.com
    lab-screenshot capture --org https://your-org.okta.com --path /admin/dashboard -o screenshot.png
    lab-screenshot run guide.md --org https://your-org.okta.com --username bot@okta.com --totp-secret ABC
    lab-screenshot check guide.md
"""

import argparse
import base64
import getpass
import json
import os
import shutil
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional


def cmd_login(args):
    """Authenticate to Okta and save a persistent browser session."""
    from lab_screenshot.screenshot import login_browser_profile

    username = args.username or input("Okta username: ").strip()
    password = args.password or getpass.getpass("Okta password: ")

    login_browser_profile(
        org_url=args.org,
        username=username,
        password=password,
        totp_secret=args.totp_secret,
        profile_dir=args.profile_dir,
    )


def cmd_capture(args):
    """Capture a single screenshot."""
    from lab_screenshot.screenshot import capture_screenshot, screenshot_to_base64

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
        sys.stdout.buffer.write(png_bytes)


def cmd_check(args):
    """Parse a guide and list markers (dry run)."""
    from lab_screenshot.guide import parse_markers

    text = Path(args.guide).read_text(encoding="utf-8")
    markers = parse_markers(text)

    if not markers:
        print("No [SCREENSHOT: ...] markers found.")
        return

    print(f"Found {len(markers)} markers in {args.guide}:\n")
    for m in markers:
        print(f"  [{m.index}] Line {m.line}: {m.description}")

    print(f"\nRun 'lab-screenshot run {args.guide} ...' to capture these.")


def cmd_run(args):
    """Process a guide end-to-end: login, navigate, capture, output."""
    from lab_screenshot.guide import parse_markers, capture_to_base64, replace_markers

    guide_path = Path(args.guide)
    if not guide_path.exists():
        print(f"ERROR: Guide not found: {guide_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else guide_path
    text = guide_path.read_text(encoding="utf-8")
    markers = parse_markers(text)

    if not markers:
        print("No [SCREENSHOT: ...] markers found. Nothing to do.")
        return

    print(f"Found {len(markers)} markers in {guide_path}")
    for m in markers:
        print(f"  [{m.index}] Line {m.line}: {m.description}")

    use_agent = getattr(args, "agent", False)

    # --- Resolve Okta org URLs ---
    org = args.org.rstrip("/")
    admin_url = org.replace(".okta.com", "-admin.okta.com") if "-admin" not in org else org
    # Also handle oktapreview.com
    if ".oktapreview.com" in org and "-admin" not in org:
        admin_url = org.replace(".oktapreview.com", "-admin.oktapreview.com")
    base_org = org.replace("-admin.okta.com", ".okta.com").replace("-admin.oktapreview.com", ".oktapreview.com") if "-admin" in org else org

    # --- Authenticate ---
    username = args.username or input("Okta username: ").strip()
    password = args.password or getpass.getpass("Okta password: ")

    print(f"\nAuthenticating as {username}...")

    try:
        import pyotp
    except ImportError:
        print("ERROR: pyotp required. pip install pyotp", file=sys.stderr)
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    # Get session token
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(f"{base_org}/api/v1/authn", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"ERROR: Authentication failed ({e.code})", file=sys.stderr)
        sys.exit(1)

    session_token = data.get("sessionToken")
    if not session_token:
        print(f"ERROR: No session token. Status: {data.get('status')}", file=sys.stderr)
        sys.exit(1)

    print("  Session token acquired")

    # --- Launch browser and complete login ---
    profile = args.profile_dir or os.path.expanduser("~/.okta-lab-screenshots/run-profile")
    shutil.rmtree(profile, ignore_errors=True)
    os.makedirs(profile, exist_ok=True)

    cookie_url = f"{base_org}/login/sessionCookieRedirect?token={session_token}&redirectUrl={admin_url}/admin/dashboard"

    images = {}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=not args.visible,
            viewport={"width": args.width, "height": args.height},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Cookie redirect
        page.goto(cookie_url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Admin console OAuth flow
        page.goto(f"{admin_url}/admin/dashboard", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Handle browser MFA (TOTP)
        page_text = page.inner_text("body")
        if "Enter a code" in page_text or "credentials.totp" in page.content():
            if not args.totp_secret:
                code = input("Enter MFA code: ").strip()
            else:
                code = pyotp.TOTP(args.totp_secret).now()
            print(f"  Entering TOTP...")
            page.fill('input[name="credentials.totp"]', code)
            page.click('input[data-type="save"]')
            page.wait_for_timeout(5000)

        # Handle "Keep me signed in"
        page_text = page.inner_text("body")
        if "Stay signed in" in page_text:
            print("  Clicking 'Stay signed in'")
            page.click('a[data-se="stay-signed-in-btn"]')
            page.wait_for_timeout(5000)

        if "/admin/" not in page.url:
            print(f"ERROR: Could not reach admin console: {page.url}", file=sys.stderr)
            context.close()
            sys.exit(1)

        print(f"  Authenticated! URL: {page.url}")

        # Dismiss popup overlays
        try:
            close_btn = page.locator('button:has-text("Close"), [aria-label="Close"]').first
            if close_btn.is_visible(timeout=2000):
                close_btn.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        if use_agent:
            # --- LLM Agent mode: agent reads guide and drives browser ---
            print(f"\nStarting LLM agent (model: {os.environ.get('LLM_MODEL', 'auto')})...")
            from .browser_agent import BrowserAgent
            agent = BrowserAgent(page=page, admin_url=admin_url)
            images = agent.process_guide(text)
        else:
            # --- Manual mode: user provides navigation ---
            print(f"\nCapturing {len(markers)} screenshots...")
            for marker in markers:
                print(f"  [{marker.index}] {marker.description}")

                if args.pages:
                    pages_list = [p.strip() for p in args.pages.split(",")]
                    if marker.index < len(pages_list):
                        nav_path = pages_list[marker.index]
                        url = f"{admin_url}{nav_path}" if nav_path.startswith("/") else nav_path
                        print(f"    Navigating to: {url}")
                        page.goto(url, wait_until="networkidle", timeout=30000)
                        page.wait_for_timeout(args.delay / 1000 * 1000)
                elif not args.no_prompt:
                    nav = input(f"    Navigate to (URL path or Enter to capture current page): ").strip()
                    if nav:
                        url = f"{admin_url}{nav}" if nav.startswith("/") else nav
                        page.goto(url, wait_until="networkidle", timeout=30000)
                        page.wait_for_timeout(2000)

                images[marker.index] = capture_to_base64(page, delay_ms=500)
                print(f"    Captured: {len(images[marker.index]):,} chars")

        context.close()

    # --- Replace markers and write output ---
    print(f"\nReplacing {len(images)} markers...")
    updated = replace_markers(text, images)

    output_path.write_text(updated, encoding="utf-8")
    print(f"Written to: {output_path}")

    # Save individual PNGs if requested
    if args.save_pngs:
        png_dir = output_path.parent / "screenshots"
        png_dir.mkdir(exist_ok=True)
        for i, data_uri in images.items():
            png = base64.b64decode(data_uri.split(",")[1])
            out = png_dir / f"screenshot-{i}.png"
            out.write_bytes(png)
            print(f"  Saved: {out} ({len(png):,} bytes)")

    print(f"\nDone! {len(images)}/{len(markers)} markers replaced.")


def cmd_record(args):
    """Record-then-extract: Pass 1 records everything, Pass 2 selects best frames via vision."""
    from .guide import parse_markers, replace_markers
    from .recorder import GuideRecorder
    from .frame_selector import select_frames

    guide_path = Path(args.guide)
    if not guide_path.exists():
        print(f"ERROR: Guide not found: {guide_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else guide_path
    text = guide_path.read_text(encoding="utf-8")
    markers = parse_markers(text)

    if not markers:
        print("No [SCREENSHOT: ...] markers found.")
        return

    print(f"Found {len(markers)} markers in {guide_path}")
    for m in markers:
        print(f"  [{m.index}] Line {m.line}: {m.description}")

    use_setup = getattr(args, "setup", False)
    no_auth = getattr(args, "no_auth", False)

    # --- Resolve org URL ---
    org = args.org.rstrip("/")

    from playwright.sync_api import sync_playwright

    profile = args.profile_dir or os.path.expanduser("~/.okta-lab-screenshots/record-profile")

    print(f"\n=== Pass 1: Record ===")

    use_chrome = getattr(args, "chrome", False)
    chrome_kwargs = {"channel": "chrome"} if use_chrome else {}

    if no_auth:
        # --- No auth: navigate directly ---
        print(f"No-auth mode — will navigate to {org}")
        admin_url = org
        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)

    elif use_setup:
        # --- Interactive setup: user authenticates in a visible browser ---
        print("Opening browser for manual authentication...")
        if use_chrome:
            print("  Using system Chrome (--chrome flag)")
        print(f"  Navigate to: {org}")
        print(f"  Log in, reach the page you want the bot to start from,")
        print(f"  then CLOSE the browser window to continue.")
        print()

        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=False,
                viewport={"width": args.width, "height": args.height},
                args=["--disable-blink-features=AutomationControlled"],
                **chrome_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(org, wait_until="networkidle", timeout=60000)

            # Wait for user to close the browser
            try:
                page.wait_for_event("close", timeout=600000)  # 10 min
            except Exception:
                pass

            context.close()

        print(f"  Session saved to: {profile}")
        print(f"  Continuing headlessly...\n")

        # Determine admin URL from what the user landed on
        # Default: use the org URL as-is (lab environments may not follow standard patterns)
        admin_url = org

    else:
        # --- Headless authentication via authn API ---
        admin_url = org.replace(".okta.com", "-admin.okta.com") if "-admin" not in org else org
        if ".oktapreview.com" in org and "-admin" not in org:
            admin_url = org.replace(".oktapreview.com", "-admin.oktapreview.com")
        base_org = org.replace("-admin.okta.com", ".okta.com").replace("-admin.oktapreview.com", ".oktapreview.com") if "-admin" in org else org

        username = args.username or input("Okta username: ").strip()
        password = args.password or getpass.getpass("Okta password: ")
        print(f"Authenticating as {username}...")

        try:
            import pyotp
        except ImportError:
            print("ERROR: pyotp required. pip install pyotp", file=sys.stderr)
            sys.exit(1)

        body = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(f"{base_org}/api/v1/authn", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            print(f"ERROR: Authentication failed ({e.code})", file=sys.stderr)
            sys.exit(1)

        session_token = data.get("sessionToken")
        if not session_token:
            print(f"ERROR: No session token. Status: {data.get('status')}", file=sys.stderr)
            sys.exit(1)

        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)

        cookie_url = f"{base_org}/login/sessionCookieRedirect?token={session_token}&redirectUrl={admin_url}/admin/dashboard"

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=not args.visible,
                viewport={"width": args.width, "height": args.height},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(cookie_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            page.goto(f"{admin_url}/admin/dashboard", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            page_text_body = page.inner_text("body")
            if "Enter a code" in page_text_body or "credentials.totp" in page.content():
                if not args.totp_secret:
                    code = input("Enter MFA code: ").strip()
                else:
                    code = pyotp.TOTP(args.totp_secret).now()
                print(f"  Entering TOTP...")
                page.fill('input[name="credentials.totp"]', code)
                page.click('input[data-type="save"]')
                page.wait_for_timeout(5000)

            page_text_body = page.inner_text("body")
            if "Stay signed in" in page_text_body:
                page.click('a[data-se="stay-signed-in-btn"]')
                page.wait_for_timeout(5000)

            print(f"  Authenticated: {page.url}")

            context.close()

    # --- Pass 1: Record (headless, using saved profile) ---
    print("Starting recording pass...")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=not args.visible,
            viewport={"width": args.width, "height": args.height},
            args=["--disable-blink-features=AutomationControlled"],
            **chrome_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()

        # Navigate to start page if not coming from setup (setup leaves the browser where the user was)
        if not use_setup:
            start_url = admin_url if no_auth else f"{admin_url}/admin/dashboard"
            page.goto(start_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

        # Dismiss any popups
        try:
            cb = page.locator('button:has-text("Close"), [aria-label="Close"]').first
            if cb.is_visible(timeout=2000):
                cb.click()
        except Exception:
            pass

        print(f"  Starting URL: {page.url}")

        recorder = GuideRecorder(
            page=page,
            context=context,
            admin_url=admin_url,
            output_dir=args.recording_dir,
        )
        recording = recorder.record_guide(text)

        context.close()

    print(f"\nPass 1 complete: {len(recording.frames)} frames captured")
    print(f"  Recording: {args.recording_dir}/")

    # --- Pass 2: Select ---
    print(f"\n=== Pass 2: Select best frames via vision ===")

    frames_meta = [
        {
            "index": f.index,
            "url": f.url,
            "title": f.title,
            "action": f.action,
            "png_path": f.png_path,
        }
        for f in recording.frames
    ]

    images = select_frames(frames_meta, markers)

    print(f"\nPass 2 complete: {len(images)}/{len(markers)} frames selected")

    # --- Replace markers ---
    print(f"\nWriting output...")
    updated = replace_markers(text, images)
    output_path.write_text(updated, encoding="utf-8")
    print(f"  Written to: {output_path}")

    if args.save_pngs:
        import base64 as b64mod
        png_dir = output_path.parent / "screenshots"
        png_dir.mkdir(exist_ok=True)
        for i, data_uri in images.items():
            png = b64mod.b64decode(data_uri.split(",")[1])
            out = png_dir / f"screenshot-{i}.png"
            out.write_bytes(png)
            print(f"  Saved: {out} ({len(png):,} bytes)")

    print(f"\nDone! {len(images)}/{len(markers)} markers replaced.")


def main():
    parser = argparse.ArgumentParser(
        prog="lab-screenshot",
        description="Automated screenshot tool for Okta admin console lab guides",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # --- login ---
    login_p = subparsers.add_parser("login", help="Authenticate and save browser session")
    login_p.add_argument("--org", required=True, help="Okta org URL")
    login_p.add_argument("--username", help="Okta username (prompts if omitted)")
    login_p.add_argument("--password", help="Okta password (prompts if omitted)")
    login_p.add_argument("--totp-secret", help="TOTP secret for automated MFA")
    login_p.add_argument("--profile-dir", help="Browser profile directory")

    # --- capture ---
    cap_p = subparsers.add_parser("capture", help="Take a single screenshot")
    cap_p.add_argument("--org", required=True, help="Okta org URL")
    cap_p.add_argument("--path", required=True, help="URL path (e.g., /admin/dashboard)")
    cap_p.add_argument("-o", "--output", help="Output PNG file")
    cap_p.add_argument("--wait-for", help="CSS selector to wait for")
    cap_p.add_argument("--wait-timeout", type=int, default=10000)
    cap_p.add_argument("--delay", type=int, default=2000, help="Delay after load (ms)")
    cap_p.add_argument("--width", type=int, default=1440, help="Viewport width")
    cap_p.add_argument("--height", type=int, default=900, help="Viewport height")
    cap_p.add_argument("--full-page", action="store_true")
    cap_p.add_argument("--base64", action="store_true", help="Output base64 data URI")
    cap_p.add_argument("--visible", action="store_true", help="Show browser window")
    cap_p.add_argument("--profile-dir", help="Browser profile directory")

    # --- check ---
    check_p = subparsers.add_parser("check", help="List markers in a guide (dry run)")
    check_p.add_argument("guide", help="Path to markdown guide")

    # --- run ---
    run_p = subparsers.add_parser("run", help="Process a guide end-to-end")
    run_p.add_argument("guide", help="Path to markdown guide")
    run_p.add_argument("--org", required=True, help="Okta org URL")
    run_p.add_argument("--username", help="Okta username (prompts if omitted)")
    run_p.add_argument("--password", help="Okta password (prompts if omitted)")
    run_p.add_argument("--totp-secret", help="TOTP secret for automated MFA")
    run_p.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    run_p.add_argument("--pages", help="Comma-separated URL paths for each marker")
    run_p.add_argument("--no-prompt", action="store_true", help="Don't prompt — capture current page at each marker")
    run_p.add_argument("--save-pngs", action="store_true", help="Save individual PNGs alongside output")
    run_p.add_argument("--width", type=int, default=1440, help="Viewport width")
    run_p.add_argument("--height", type=int, default=900, help="Viewport height")
    run_p.add_argument("--delay", type=int, default=2000, help="Delay after navigation (ms)")
    run_p.add_argument("--visible", action="store_true", help="Show browser window")
    run_p.add_argument("--profile-dir", help="Browser profile directory")
    run_p.add_argument("--agent", action="store_true", help="Use LLM agent to drive browser (reads guide steps, navigates autonomously)")


    # --- record ---
    rec_p = subparsers.add_parser("record", help="Record-then-extract: execute guide, record everything, select best frames via vision")
    rec_p.add_argument("guide", help="Path to markdown guide")
    rec_p.add_argument("--org", required=True, help="Okta org URL")
    rec_p.add_argument("--username", help="Okta username (prompts if omitted)")
    rec_p.add_argument("--password", help="Okta password (prompts if omitted)")
    rec_p.add_argument("--totp-secret", help="TOTP secret for automated MFA")
    rec_p.add_argument("-o", "--output", help="Output file (default: overwrite input)")
    rec_p.add_argument("--recording-dir", default="/tmp/lab-screenshot-recording", help="Directory for recording frames")
    rec_p.add_argument("--save-pngs", action="store_true", help="Save selected PNGs alongside output")
    rec_p.add_argument("--width", type=int, default=1440, help="Viewport width")
    rec_p.add_argument("--height", type=int, default=900, help="Viewport height")
    rec_p.add_argument("--visible", action="store_true", help="Show browser window")
    rec_p.add_argument("--profile-dir", help="Browser profile directory")
    rec_p.add_argument("--setup", action="store_true", help="Open a visible browser first for manual login, then continue headlessly")
    rec_p.add_argument("--no-auth", action="store_true", help="Skip authentication — use for public sites or pre-authenticated sessions")

    # --- app ---
    app_p = subparsers.add_parser("app", help="Launch desktop app with web UI")
    app_p.add_argument("--port", type=int, default=8384, help="Port for the local web server")
    rec_p.add_argument("--chrome", action="store_true", help="Use system Chrome instead of Playwright's Chromium (avoids corporate endpoint blocks)")

    args = parser.parse_args()

    if args.command == "login":
        cmd_login(args)
    elif args.command == "capture":
        cmd_capture(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "record":
        cmd_record(args)
    elif args.command == "app":
        from .app import run_app
        run_app(port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
