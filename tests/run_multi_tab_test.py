#!/usr/bin/env python3
"""
Automated multi-tab test suite for the lab-screenshot recorder.

Runs the mock lab server locally, then executes the recorder against it
multiple times in sequence to test consistency.

Usage:
    AWS_PROFILE=taskvantage AWS_REGION=us-west-2 \
    LLM_MODEL="bedrock/us.anthropic.claude-sonnet-4-6" \
    python3 tests/run_multi_tab_test.py [--iterations 3]

Checks:
1. Did the agent navigate to all required pages?
2. Did it handle the multi-tab Launch button?
3. Did it fill in the settings form?
4. Did Pass 2 vision select the right frames?
5. Were all markers replaced in the output?
"""

import json
import os
import shutil
import subprocess
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ITERATIONS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 3
MOCK_PORT = 8765
GUIDE_PATH = Path(__file__).parent / "mock-lab-guide.md"
BASE_URL = f"http://localhost:{MOCK_PORT}"


def start_mock_server():
    """Start the mock lab server in a background thread."""
    from tests.mock_lab_server import start_server
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(1)  # Let server start
    return t


def run_iteration(iteration: int) -> dict:
    """Run one iteration of the test and return results."""
    output_dir = Path(f"/tmp/lab-screenshot-test-{iteration}")
    recording_dir = output_dir / "recording"
    output_md = output_dir / "output.md"
    screenshots_dir = output_dir / "screenshots"

    # Clean previous run
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    print(f"\n{'='*60}")
    print(f"  ITERATION {iteration}")
    print(f"{'='*60}")

    # Run the recorder
    cmd = [
        sys.executable, "-m", "lab_screenshot.cli",
        "record", str(GUIDE_PATH),
        "--org", BASE_URL,
        "--no-auth",
        "-o", str(output_md),
        "--save-pngs",
        "--recording-dir", str(recording_dir),
    ]

    start = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(Path(__file__).parent.parent),
    )
    elapsed = time.time() - start

    print(f"  Exit code: {result.returncode}")
    print(f"  Duration: {elapsed:.1f}s")

    # Parse results
    stdout = result.stdout
    stderr = result.stderr

    # Count frames captured
    frame_count = stderr.count("[recorder] frame ")
    print(f"  Frames captured: {frame_count}")

    # Count markers replaced
    if output_md.exists():
        output_text = output_md.read_text()
        import re
        replaced = len(re.findall(r'!\[.+?\]\(data:image', output_text))
        remaining = len(re.findall(r'\[SCREENSHOT: .+?\]', output_text))
    else:
        replaced = 0
        remaining = 4

    print(f"  Markers replaced: {replaced}/4")
    print(f"  Markers remaining: {remaining}")

    # Check specific navigation actions
    nav_actions = {
        "launch_click": "Launch" in stderr or "launch" in stderr.lower(),
        "new_tab_detected": "NEW TAB" in stderr or "new_tab" in stderr,
        "users_page": "/app/users" in stderr,
        "settings_page": "/app/settings" in stderr,
        "form_fill": "fill:" in stderr,
        "save_click": "Save" in stderr,
        "dashboard_return": stderr.count("/app") >= 2,  # visited /app more than once
    }

    for action, found in nav_actions.items():
        status = "YES" if found else "NO"
        print(f"  {action}: {status}")

    # Check recording frames
    if recording_dir.exists():
        frame_files = sorted(recording_dir.glob("frame-*.png"))
        print(f"  Frame files saved: {len(frame_files)}")

        # Check recording metadata
        meta_path = recording_dir / "recording.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            urls_visited = set(f["url"] for f in meta["frames"])
            print(f"  Unique URLs visited: {len(urls_visited)}")
            for url in sorted(urls_visited):
                print(f"    - {url[:80]}")

    # Check screenshots
    if screenshots_dir.exists():
        screenshots = sorted(screenshots_dir.glob("*.png"))
        print(f"  Selected screenshots: {len(screenshots)}")
        for s in screenshots:
            print(f"    - {s.name} ({s.stat().st_size:,} bytes)")

    # Print stderr summary (last 20 lines)
    stderr_lines = stderr.strip().split("\n")
    if len(stderr_lines) > 20:
        print(f"\n  Last 20 lines of stderr:")
        for line in stderr_lines[-20:]:
            print(f"    {line}")

    return {
        "iteration": iteration,
        "exit_code": result.returncode,
        "duration": round(elapsed, 1),
        "frames": frame_count,
        "replaced": replaced,
        "remaining": remaining,
        "nav_actions": nav_actions,
    }


def main():
    print("="*60)
    print("  Lab Screenshot — Multi-Tab Test Suite")
    print(f"  Iterations: {ITERATIONS}")
    print(f"  Guide: {GUIDE_PATH}")
    print(f"  Mock server: {BASE_URL}")
    print(f"  LLM: {os.environ.get('LLM_MODEL', 'default')}")
    print("="*60)

    # Check guide
    from lab_screenshot.guide import parse_markers
    text = GUIDE_PATH.read_text()
    markers = parse_markers(text)
    print(f"\nGuide has {len(markers)} markers:")
    for m in markers:
        print(f"  [{m.index}] {m.description}")

    # Start mock server
    print(f"\nStarting mock lab server on port {MOCK_PORT}...")
    start_mock_server()

    # Verify server is running
    import urllib.request
    try:
        with urllib.request.urlopen(BASE_URL) as resp:
            assert resp.status == 200
        print("  Server is running!")
    except Exception as e:
        print(f"  ERROR: Server not responding: {e}")
        sys.exit(1)

    # Run iterations
    results = []
    for i in range(1, ITERATIONS + 1):
        try:
            r = run_iteration(i)
            results.append(r)
        except subprocess.TimeoutExpired:
            print(f"\n  ITERATION {i}: TIMEOUT (300s)")
            results.append({"iteration": i, "exit_code": -1, "replaced": 0, "remaining": 4, "frames": 0})
        except Exception as e:
            print(f"\n  ITERATION {i}: ERROR: {e}")
            results.append({"iteration": i, "exit_code": -1, "replaced": 0, "remaining": 4, "frames": 0})

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY ({ITERATIONS} iterations)")
    print(f"{'='*60}")
    print(f"  {'Iter':>4s}  {'Exit':>4s}  {'Time':>6s}  {'Frames':>6s}  {'Replaced':>8s}  {'Remaining':>9s}")
    for r in results:
        print(f"  {r['iteration']:>4d}  {r['exit_code']:>4d}  {r.get('duration',0):>5.1f}s  {r['frames']:>6d}  {r['replaced']:>5d}/4   {r['remaining']:>5d}/4")

    # Consistency check
    replaced_counts = [r["replaced"] for r in results]
    frame_counts = [r["frames"] for r in results]
    consistent = len(set(replaced_counts)) == 1 and all(r["replaced"] == 4 for r in results)
    print(f"\n  All markers replaced consistently: {'YES' if consistent else 'NO'}")
    print(f"  Replacement range: {min(replaced_counts)}-{max(replaced_counts)}/4")
    print(f"  Frame count range: {min(frame_counts)}-{max(frame_counts)}")


if __name__ == "__main__":
    main()
