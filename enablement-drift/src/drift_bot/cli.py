"""
CLI entry point for the drift detection bot.

Commands:
  drift-bot interpret <guide.md> --out expectations.yaml
  drift-bot capture <expectations.yaml> --org <url> --out captured_state.json
  drift-bot compare <expectations.yaml> <captured_state.json> --out drift_report.json
  drift-bot run <guide.md> --org <url>  (full pipeline)
"""

import asyncio
import json
import time
import uuid
from pathlib import Path

import typer

app = typer.Typer(name="drift-bot", help="Documentation drift detection for Okta admin console")


@app.command()
def interpret(
    guide: str = typer.Argument(help="Path to markdown lab guide"),
    out: str = typer.Option("expectations.yaml", help="Output file path"),
    model: str = typer.Option(None, help="LLM model override"),
):
    """Extract expected UI state from a markdown lab guide."""
    from .agents.interpreter import interpret as do_interpret

    print(f"📖 Interpreting: {guide}")
    t0 = time.time()

    expectations = do_interpret(guide, model=model)

    Path(out).write_text(expectations.to_yaml())
    print(f"✅ Wrote {len(expectations.expectations)} expectations to {out} ({time.time()-t0:.1f}s)")


@app.command()
def capture(
    expectations_path: str = typer.Argument(help="Path to expectations.yaml"),
    org: str = typer.Option("", help="Okta admin org URL"),
    username: str = typer.Option("", help="Okta admin username"),
    password: str = typer.Option("", help="Okta admin password"),
    totp_secret: str = typer.Option("", help="TOTP secret for MFA"),
    out: str = typer.Option("captured_state.json", help="Output file path"),
    headed: bool = typer.Option(False, help="Run browser in headed mode"),
    run_id: str = typer.Option("", help="Force a specific run ID"),
):
    """Capture actual UI state from the live Okta admin console."""
    from .agents.navigator import capture as do_capture
    from .models.expectations import DocExpectations

    print(f"🔍 Loading expectations: {expectations_path}")
    yaml_text = Path(expectations_path).read_text()
    expectations = DocExpectations.from_yaml(yaml_text)
    print(f"   {len(expectations.expectations)} steps to capture")

    print(f"🌐 Capturing from: {org or 'env OKTA_ORG_URL'}")
    t0 = time.time()

    doc_capture = asyncio.run(do_capture(
        expectations=expectations,
        org_url=org,
        username=username,
        password=password,
        totp_secret=totp_secret,
        run_id=run_id or str(uuid.uuid4())[:8],
        headless=not headed,
    ))

    capture_json = doc_capture.model_dump_json(indent=2)
    # Don't persist base64 screenshots in the JSON (too large)
    capture_data = json.loads(capture_json)
    for c in capture_data.get("captures", []):
        c.pop("screenshot_base64", None)
    Path(out).write_text(json.dumps(capture_data, indent=2))

    success = sum(1 for c in doc_capture.captures if not c.error)
    print(f"✅ Captured {success}/{len(doc_capture.captures)} steps ({time.time()-t0:.1f}s)")
    print(f"   Output: {out}")
    if doc_capture.captures:
        print(f"   Screenshots: {doc_capture.captures[0].screenshot_path.rsplit('/', 1)[0] if doc_capture.captures[0].screenshot_path else 'N/A'}")


@app.command()
def compare(
    expectations_path: str = typer.Argument(help="Path to expectations.yaml"),
    capture_path: str = typer.Argument(help="Path to captured_state.json"),
    out: str = typer.Option("drift_report.json", help="Output file path"),
):
    """Compare expected vs captured state to find drift."""
    from .agents.comparator import compare as do_compare
    from .models.expectations import DocExpectations
    from .models.capture import DocCapture

    print(f"📊 Comparing expectations vs captured state")

    expectations = DocExpectations.from_yaml(Path(expectations_path).read_text())
    capture_data = json.loads(Path(capture_path).read_text())
    doc_capture = DocCapture.model_validate(capture_data)

    t0 = time.time()
    report = do_compare(expectations, doc_capture)

    Path(out).write_text(report.model_dump_json(indent=2))
    md_path = out.replace(".json", ".md")
    Path(md_path).write_text(report.to_markdown())

    print(f"✅ Comparison complete ({time.time()-t0:.1f}s)")
    print(f"   Labels checked: {report.total_labels_checked}")
    print(f"   Drift found: {report.drift_count}")
    print(f"   Auto-mergeable: {report.auto_merge_count}")
    print(f"   Needs review: {report.needs_review_count}")
    print(f"   Report: {out}")
    print(f"   Summary: {md_path}")


@app.command()
def run(
    guide: str = typer.Argument(help="Path to markdown lab guide"),
    org: str = typer.Option("", help="Okta admin org URL"),
    username: str = typer.Option("", help="Okta admin username"),
    password: str = typer.Option("", help="Okta admin password"),
    totp_secret: str = typer.Option("", help="TOTP secret for MFA"),
    model: str = typer.Option(None, help="LLM model override"),
    headed: bool = typer.Option(False, help="Run browser in headed mode"),
    run_id: str = typer.Option("", help="Force a specific run ID"),
):
    """Run the full drift detection pipeline: interpret → capture → compare."""
    from .agents.interpreter import interpret as do_interpret
    from .agents.navigator import capture as do_capture
    from .agents.comparator import compare as do_compare
    from .config import RUNS_DIR

    rid = run_id or str(uuid.uuid4())[:8]
    run_dir = RUNS_DIR / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"{'='*60}")
    print(f"  🔎 DRIFT DETECTION BOT — Run {rid}")
    print(f"{'='*60}")
    print()

    # Phase 1: Interpret
    print(f"📖 PHASE 1: INTERPRETING GUIDE")
    print(f"   Source: {guide}")
    t0 = time.time()
    expectations = do_interpret(guide, model=model)
    exp_path = run_dir / "expectations.yaml"
    exp_path.write_text(expectations.to_yaml())
    print(f"   ✅ {len(expectations.expectations)} expectations extracted ({time.time()-t0:.1f}s)")
    print()

    # Phase 2: Capture
    print(f"🌐 PHASE 2: CAPTURING LIVE STATE")
    print(f"   Org: {org or 'env OKTA_ORG_URL'}")
    t1 = time.time()
    doc_capture = asyncio.run(do_capture(
        expectations=expectations,
        org_url=org,
        username=username,
        password=password,
        totp_secret=totp_secret,
        run_id=rid,
        headless=not headed,
    ))
    capture_path = run_dir / "captured_state.json"
    capture_data = json.loads(doc_capture.model_dump_json())
    for c in capture_data.get("captures", []):
        c.pop("screenshot_base64", None)
    capture_path.write_text(json.dumps(capture_data, indent=2))
    success = sum(1 for c in doc_capture.captures if not c.error)
    print(f"   ✅ {success}/{len(doc_capture.captures)} steps captured ({time.time()-t1:.1f}s)")
    print()

    # Phase 3: Compare
    print(f"📊 PHASE 3: DETECTING DRIFT")
    t2 = time.time()
    report = do_compare(expectations, doc_capture)
    report_path = run_dir / "drift_report.json"
    report_path.write_text(report.model_dump_json(indent=2))
    summary_path = run_dir / "summary_report.md"
    summary_path.write_text(report.to_markdown())
    print(f"   ✅ Complete ({time.time()-t2:.1f}s)")
    print()

    # Summary
    print(f"{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Steps checked:   {report.total_expectations}")
    print(f"  Labels checked:  {report.total_labels_checked}")
    print(f"  Drift found:     {report.drift_count}")
    print(f"  Auto-mergeable:  {report.auto_merge_count}")
    print(f"  Needs review:    {report.needs_review_count}")
    print()
    for f in report.findings:
        emoji = "🔴" if f.severity == "high" else "🟡" if f.severity == "medium" else "🟢"
        print(f"  {emoji} [{f.drift_class}] {f.expected} → {f.observed or '(missing)'} ({f.confidence:.0%})")
    print()
    print(f"  📁 Artifacts: {run_dir}")
    print(f"  📄 Summary:   {summary_path}")
    total = time.time() - t0
    print(f"  ⏱️  Total time: {total:.1f}s")


def main():
    app()


if __name__ == "__main__":
    main()
