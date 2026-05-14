#!/usr/bin/env python3
"""
Automated test harness for the drift detection pipeline.

Runs the full pipeline against taskvantage-admin.okta.com and validates:
1. Interpreter produces valid expectations from the seeded guide
2. Navigator successfully captures state from the live console
3. Comparator correctly identifies seeded drift

Run: python -m tests.test_pipeline
  or: python tests/test_pipeline.py
"""

import sys
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from drift_bot.agents.interpreter import interpret
from drift_bot.agents.navigator import capture
from drift_bot.agents.comparator import compare
from drift_bot.models.expectations import DocExpectations
from drift_bot.models.capture import DocCapture

# Config
GUIDE_PATH = str(Path(__file__).parent.parent / "guides" / "seeded-drift-guide.md")
ORG_URL = os.getenv("OKTA_ORG_URL", "https://taskvantage-admin.okta.com")
USERNAME = os.getenv("OKTA_USERNAME", "mcp-testbot@atko.email")
PASSWORD = os.getenv("OKTA_PASSWORD", "MCPtest1234!@#")
TOTP_SECRET = os.getenv("OKTA_TOTP_SECRET", "YIAL7W6STGNYEILX")
RESULTS_DIR = Path(__file__).parent.parent / "test-results"


def test_interpreter():
    """Test that the interpreter extracts valid expectations from the guide."""
    print("\n" + "=" * 60)
    print("  TEST 1: INTERPRETER")
    print("=" * 60)

    expectations = interpret(GUIDE_PATH)

    print(f"  Doc title: {expectations.doc_title}")
    print(f"  Steps extracted: {len(expectations.expectations)}")

    assert len(expectations.expectations) > 0, "No expectations extracted"

    for exp in expectations.expectations:
        print(f"\n  Step {exp.step_id}: {exp.description[:60]}...")
        print(f"    Navigation: {' > '.join(exp.navigation) if exp.navigation else 'none'}")
        print(f"    URL hint: {exp.url_hint or 'none'}")
        print(f"    Labels: {len(exp.labels)}")
        for lbl in exp.labels:
            print(f"      - [{lbl.semantic_role}] \"{lbl.text}\"")

    # Validate the seeded drift labels are in the expectations
    all_labels = [lbl.text for exp in expectations.expectations for lbl in exp.labels]
    print(f"\n  All labels: {all_labels}")

    # The guide mentions "Governance Engine" which is our seeded drift
    has_governance_engine = any("Governance Engine" in l for l in all_labels)
    print(f"\n  Contains 'Governance Engine' (seeded drift): {has_governance_engine}")

    # Save for next phases
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    yaml_path = RESULTS_DIR / "expectations.yaml"
    yaml_path.write_text(expectations.to_yaml())
    print(f"\n  Saved: {yaml_path}")

    return expectations


def test_navigator(expectations: DocExpectations):
    """Test that the navigator captures state from the live console."""
    print("\n" + "=" * 60)
    print("  TEST 2: NAVIGATOR")
    print("=" * 60)

    doc_capture = asyncio.run(capture(
        expectations=expectations,
        org_url=ORG_URL,
        username=USERNAME,
        password=PASSWORD,
        totp_secret=TOTP_SECRET,
        run_id="test-" + datetime.now().strftime("%H%M%S"),
        headless=True,
    ))

    print(f"\n  Org: {doc_capture.org_url}")
    print(f"  Run ID: {doc_capture.run_id}")
    print(f"  Steps captured: {len(doc_capture.captures)}")

    success = 0
    for cap in doc_capture.captures:
        if cap.error:
            print(f"\n  ❌ Step {cap.step_id}: ERROR — {cap.error}")
        else:
            success += 1
            print(f"\n  ✅ Step {cap.step_id}:")
            print(f"    URL: {cap.url}")
            print(f"    Labels found: {len(cap.accessible_labels)}")
            # Show first 10 labels
            for lbl in cap.accessible_labels[:10]:
                print(f"      - [{lbl.tag}/{lbl.role}] \"{lbl.text[:60]}\"")
            if len(cap.accessible_labels) > 10:
                print(f"      ... and {len(cap.accessible_labels) - 10} more")

    print(f"\n  Success rate: {success}/{len(doc_capture.captures)}")

    # Save for next phase
    capture_data = json.loads(doc_capture.model_dump_json())
    for c in capture_data.get("captures", []):
        c.pop("screenshot_base64", None)
    capture_path = RESULTS_DIR / "captured_state.json"
    capture_path.write_text(json.dumps(capture_data, indent=2))
    print(f"  Saved: {capture_path}")

    return doc_capture


def test_comparator(expectations: DocExpectations, doc_capture: DocCapture):
    """Test that the comparator correctly identifies drift."""
    print("\n" + "=" * 60)
    print("  TEST 3: COMPARATOR")
    print("=" * 60)

    report = compare(expectations, doc_capture)

    print(f"\n  Labels checked: {report.total_labels_checked}")
    print(f"  Drift found: {report.drift_count}")
    print(f"  Auto-mergeable: {report.auto_merge_count}")
    print(f"  Needs review: {report.needs_review_count}")

    for f in report.findings:
        emoji = "🔴" if f.severity == "high" else "🟡" if f.severity == "medium" else "🟢"
        print(f"\n  {emoji} [{f.drift_class}] Step {f.step_id}")
        print(f"    Expected: \"{f.expected}\"")
        print(f"    Observed: \"{f.observed}\"")
        print(f"    Confidence: {f.confidence:.0%}")
        print(f"    Auto-merge: {f.auto_merge_eligible}")
        print(f"    Reasoning: {f.reasoning}")
        if f.suggested_correction:
            print(f"    Suggested: {f.suggested_correction}")

    # Save report
    report_path = RESULTS_DIR / "drift_report.json"
    report_path.write_text(report.model_dump_json(indent=2))
    summary_path = RESULTS_DIR / "summary_report.md"
    summary_path.write_text(report.to_markdown())
    print(f"\n  Report: {report_path}")
    print(f"  Summary: {summary_path}")

    return report


def run_all():
    """Run all tests in sequence."""
    print("\n" + "=" * 60)
    print("  🔎 DRIFT DETECTION PIPELINE TEST")
    print(f"  Target: {ORG_URL}")
    print(f"  Guide: {GUIDE_PATH}")
    print(f"  Time: {datetime.now().isoformat()}")
    print("=" * 60)

    t0 = __import__("time").time()

    # Phase 1
    expectations = test_interpreter()

    # Phase 2
    doc_capture = test_navigator(expectations)

    # Phase 3
    report = test_comparator(expectations, doc_capture)

    total = __import__("time").time() - t0
    print("\n" + "=" * 60)
    print(f"  COMPLETE — {total:.1f}s total")
    print(f"  Results: {RESULTS_DIR}")
    print("=" * 60)

    return report


if __name__ == "__main__":
    run_all()
