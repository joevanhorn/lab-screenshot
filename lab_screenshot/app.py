#!/usr/bin/env python3
"""
app.py — Desktop app: local FastAPI server with web UI.

Serves a browser-based GUI on localhost. The user picks a guide file,
configures their LLM API key, and clicks Record. The backend drives
the existing recorder pipeline.

Usage:
    lab-screenshot app              # Opens browser to http://localhost:8384
    lab-screenshot app --port 9000  # Custom port
"""

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


app = FastAPI(title="Lab Screenshot")


@app.on_event("startup")
async def _capture_loop():
    global _main_loop
    _main_loop = asyncio.get_event_loop()


# Global state for the current job
_current_job = {
    "status": "idle",  # idle, setup, recording, selecting, done, error
    "progress": [],
    "result": None,
    "guide_path": None,
    "output_path": None,
    # Video-recording state (only populated when record_video is enabled)
    "video_enabled": False,
    "video_log_entries": [],   # [{"t": float, "level": str, "message": str, "wall": "HH:MM:SS"}]
    "video_start_time": None,  # epoch seconds; reference for the JSONL t offsets
}

_websocket_clients: list[WebSocket] = []


async def broadcast(msg: dict):
    """Send a message to all connected WebSocket clients."""
    for ws in _websocket_clients[:]:
        try:
            await ws.send_json(msg)
        except Exception:
            _websocket_clients.remove(ws)


_main_loop = None  # Set when uvicorn starts

def log_progress(message: str, level: str = "info"):
    """Log a progress message and broadcast to UI.

    When video recording is enabled, also captures a timestamped entry into
    _current_job["video_log_entries"] so the side-by-side viewer can sync log
    rows to video playback time.
    """
    wall = time.strftime("%H:%M:%S")
    entry = {"time": wall, "level": level, "message": message}
    _current_job["progress"].append(entry)
    # Capture a timestamped copy for the video viewer if recording is on.
    if _current_job.get("video_enabled") and _current_job.get("video_start_time") is not None:
        _current_job["video_log_entries"].append({
            "t": round(time.time() - _current_job["video_start_time"], 3),
            "level": level,
            "message": message,
            "wall": wall,
        })
    # Broadcast to WebSocket clients — works from any thread
    try:
        if _main_loop and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(broadcast({"type": "progress", **entry}), _main_loop)
    except Exception:
        pass


# ---- API Routes ----

@app.get("/")
async def index():
    return HTMLResponse(APP_HTML)


@app.get("/api/status")
async def get_status():
    return JSONResponse({
        "status": _current_job["status"],
        "progress": _current_job["progress"][-20:],
        "result": _current_job["result"],
    })


@app.post("/api/upload-guide")
async def upload_guide(file: UploadFile = File(...)):
    """Upload a markdown guide file."""
    upload_dir = Path("/tmp/lab-screenshot-app")
    upload_dir.mkdir(exist_ok=True)

    guide_path = upload_dir / file.filename
    content = await file.read()
    guide_path.write_bytes(content)

    # Parse markers
    from .guide import parse_markers
    text = content.decode("utf-8")
    markers = parse_markers(text)

    _current_job["guide_path"] = str(guide_path)
    _current_job["output_path"] = str(upload_dir / f"{guide_path.stem}-output{guide_path.suffix}")

    return JSONResponse({
        "filename": file.filename,
        "size": len(content),
        "markers": [{"index": m.index, "line": m.line, "description": m.description} for m in markers],
    })


@app.post("/api/start")
async def start_recording(
    org_url: str = Form(...),
    llm_provider: str = Form("anthropic"),
    api_key: str = Form(""),
    api_base: str = Form(""),
    model: str = Form(""),
    use_chrome: bool = Form(False),
    okta_api_key: str = Form(""),
    max_per_section: int = Form(25),
    record_video: bool = Form(False),
):
    """Start the record-then-extract pipeline."""
    if _current_job["status"] not in ("idle", "done", "error"):
        return JSONResponse({"error": "A job is already running"}, status_code=409)

    if not _current_job["guide_path"]:
        return JSONResponse({"error": "Upload a guide first"}, status_code=400)

    # Set LLM environment — always use the shared LiteLLM proxy
    os.environ["LITELLM_API_BASE"] = api_base or "https://llm.atko.ai"
    os.environ["LITELLM_API_KEY"] = api_key or "sk-m4Lc0YlvjR0cjmDTR1qrJw"
    os.environ["LLM_MODEL"] = model or "claude-sonnet-4-6"

    # Store Okta API key if provided
    _current_job["okta_api_key"] = okta_api_key.strip() if okta_api_key else ""

    # Reset state
    _current_job["status"] = "setup"
    _current_job["progress"] = []
    _current_job["result"] = None

    # Clamp per-section iterations to a sane range to avoid foot-guns from manual edits.
    max_per_section = max(15, min(100, int(max_per_section or 25)))

    # Run in background thread
    thread = threading.Thread(
        target=_run_pipeline,
        args=(org_url, use_chrome, max_per_section, record_video),
        daemon=True,
    )
    thread.start()

    return JSONResponse({"status": "started"})


@app.post("/api/handoff")
async def handoff_to_bot():
    """Signal that the user has finished authenticating and the bot should take over."""
    if _current_job["status"] != "setup":
        return JSONResponse({"error": "Not in setup phase"}, status_code=400)
    _current_job["status"] = "recording"
    return JSONResponse({"status": "handoff"})


@app.post("/api/stop")
async def stop_recording():
    """Stop the current job."""
    _current_job["status"] = "idle"
    return JSONResponse({"status": "stopped"})


@app.post("/api/human-response")
async def human_response(answer: str = Form("")):
    """Respond to a bot question (ask_human tool)."""
    _current_job["human_answer"] = answer or "Continue"
    return JSONResponse({"status": "ok"})


@app.get("/api/download")
async def download_output():
    """Download the output markdown file."""
    path = _current_job.get("output_path")
    if path and Path(path).exists():
        return FileResponse(path, filename=Path(path).name, media_type="text/markdown")
    return JSONResponse({"error": "No output available"}, status_code=404)


@app.get("/api/download-recording")
async def download_recording():
    """Download the recording frames as a zip."""
    import zipfile
    import io

    recording_dir = Path("/tmp/lab-screenshot-app/recording")
    if not recording_dir.exists():
        return JSONResponse({"error": "No recording available"}, status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(recording_dir.glob("frame-*.png")):
            zf.write(str(f), f.name)
        meta = recording_dir / "recording.json"
        if meta.exists():
            zf.write(str(meta), "recording.json")

    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=lab-screenshot-recording.zip"}
    )


@app.get("/api/download-video")
async def download_video_bundle():
    """Download a zip with the session video(s), synced log JSONL, and HTML viewer.

    Available only when the run was started with 'Record video' enabled.
    """
    import zipfile
    import io

    recording_dir = Path("/tmp/lab-screenshot-app/recording")
    video_dir = recording_dir / "video"
    viewer = recording_dir / "recording-viewer.html"
    log = recording_dir / "recording-log.jsonl"

    if not video_dir.exists() or not viewer.exists():
        return JSONResponse({"error": "No video bundle available — was the run started with 'Record video' on?"}, status_code=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(str(viewer), "recording-viewer.html")
        if log.exists():
            zf.write(str(log), "recording-log.jsonl")
        for webm in sorted(video_dir.glob("*.webm")):
            zf.write(str(webm), f"video/{webm.name}")

    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=lab-screenshot-video.zip"}
    )


@app.get("/api/debug-bundle")
async def download_debug_bundle():
    """Download a zip with input guide, output, and logs for bug reporting."""
    import zipfile
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Input guide
        guide_path = _current_job.get("guide_path")
        if guide_path and Path(guide_path).exists():
            zf.write(guide_path, f"input-guide{Path(guide_path).suffix}")

        # Output
        output_path = _current_job.get("output_path")
        if output_path and Path(output_path).exists():
            zf.write(output_path, f"output{Path(output_path).suffix}")

        # Console log
        log_text = "\n".join(
            f"[{e['time']}] [{e.get('level', 'info')}] {e['message']}"
            for e in _current_job.get("progress", [])
        )
        if log_text:
            zf.writestr("console-log.txt", log_text)

        # Recording metadata
        recording_dir = Path("/tmp/lab-screenshot-app/recording")
        meta_path = recording_dir / "recording.json"
        if meta_path.exists():
            zf.write(str(meta_path), "recording-metadata.json")

        # Synced log JSONL (only present when video recording was enabled)
        synced_log = recording_dir / "recording-log.jsonl"
        if synced_log.exists():
            zf.write(str(synced_log), "recording-log.jsonl")

    buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=lab-screenshot-debug-bundle.zip"}
    )


@app.get("/preview")
async def preview_output():
    """Render the output markdown as HTML with embedded images."""
    path = _current_job.get("output_path")
    if not path or not Path(path).exists():
        return HTMLResponse("<h1>No output available yet</h1><p>Run a guide first.</p>")
    md_content = Path(path).read_text(encoding="utf-8")
    # Simple markdown → HTML conversion (handles images, headers, paragraphs)
    import re
    html = md_content
    # Headers
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    # Images (base64 embedded)
    html = re.sub(r'!\[([^\]]*)\]\((data:image/[^)]+)\)', r'<figure><img src="\2" alt="\1" style="max-width:100%;border:1px solid #e0e0e0;border-radius:8px;margin:16px 0;"><figcaption style="color:#666;font-size:13px;margin-top:4px;">\1</figcaption></figure>', html)
    # Bold
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    # Italic
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
    # Tables
    html = re.sub(r'^\|(.+)\|$', lambda m: '<tr>' + ''.join(f'<td style="padding:8px;border:1px solid #e0e0e0;">{c.strip()}</td>' for c in m.group(1).split('|')) + '</tr>', html, flags=re.MULTILINE)
    html = re.sub(r'(<tr>.*?</tr>\n?)+', r'<table style="border-collapse:collapse;margin:16px 0;">\g<0></table>', html)
    # Line breaks
    html = html.replace('\n\n', '</p><p>').replace('\n', '<br>')
    return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Guide Preview</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 24px; line-height: 1.7; color: #1e293b; }}
h1 {{ border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
h2 {{ color: #334155; margin-top: 32px; }}
p {{ color: #475569; }}
table {{ width: 100%; }}
</style></head><body><p>{html}</p></body></html>""")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _websocket_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _websocket_clients.remove(ws)


# ---- Video viewer bundle ----

def _build_video_bundle(video_dir: Path, recording_dir: Path, log_entries: list[dict]) -> None:
    """Write recording-log.jsonl and recording-viewer.html alongside the captured webms.

    The viewer pairs the largest webm in `video_dir` (assumed to be the main page,
    where the bot spends most of its time) with the synced log. Other webms remain
    in the bundle for users who want to inspect secondary tabs manually.
    """
    # Persist the synced log
    jsonl_path = recording_dir / "recording-log.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for e in log_entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    # Pick the primary video — largest webm by size (heuristic: main tab has most content)
    webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_size, reverse=True)
    primary = webms[0].name if webms else ""
    others = [w.name for w in webms[1:]] if len(webms) > 1 else []

    # Embed the log inline so the viewer is a single self-contained file.
    log_json = json.dumps(log_entries, ensure_ascii=False)
    others_json = json.dumps(others)

    viewer_path = recording_dir / "recording-viewer.html"
    viewer_path.write_text(_VIDEO_VIEWER_HTML
        .replace("__PRIMARY_VIDEO__", primary)
        .replace("__OTHER_VIDEOS_JSON__", others_json)
        .replace("__LOG_JSON__", log_json), encoding="utf-8")


_VIDEO_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Lab Screenshot — Session Recording</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; display: flex; flex-direction: column; }
  header { padding: 10px 16px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 14px; font-weight: 600; }
  header .meta { font-size: 12px; color: #94a3b8; }
  header select { margin-left: auto; padding: 4px 8px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; font-size: 12px; }
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
  .log-entry .lvl.human { color: #fbbf24; }
  .log-entry .lvl.agent { color: #34d399; }
  .log-entry .msg { color: inherit; }
  .empty { padding: 24px; text-align: center; color: #64748b; }
</style>
</head>
<body>
<header>
  <h1>Lab Screenshot — Session Recording</h1>
  <span class="meta">Click any log row to jump the video to that moment</span>
  <select id="video-picker"></select>
</header>
<main>
  <div class="video-pane">
    <video id="player" controls></video>
  </div>
  <div class="log-pane" id="log-pane"></div>
</main>
<script>
const PRIMARY = "__PRIMARY_VIDEO__";
const OTHERS = __OTHER_VIDEOS_JSON__;
const LOG = __LOG_JSON__;

const player = document.getElementById('player');
const pane = document.getElementById('log-pane');
const picker = document.getElementById('video-picker');

function fmtTime(s) {
  if (s == null) return '--:--';
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
}

function renderLog() {
  pane.innerHTML = '';
  if (!LOG || LOG.length === 0) {
    const div = document.createElement('div');
    div.className = 'empty';
    div.textContent = 'No log entries captured.';
    pane.appendChild(div);
    return;
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
}

function highlight(currentT) {
  const rows = pane.querySelectorAll('.log-entry');
  let active = -1;
  for (let i = 0; i < rows.length; i++) {
    const t = parseFloat(rows[i].dataset.t);
    if (t <= currentT) active = i; else break;
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
}

function loadVideo(name) {
  if (!name) return;
  player.src = 'video/' + name;
  player.load();
}

if (PRIMARY) {
  picker.innerHTML = '<option value="' + PRIMARY + '">' + PRIMARY + ' (primary)</option>'
    + OTHERS.map(n => '<option value="' + n + '">' + n + '</option>').join('');
  picker.addEventListener('change', () => loadVideo(picker.value));
  loadVideo(PRIMARY);
} else {
  picker.innerHTML = '<option>(no video found)</option>';
}

player.addEventListener('timeupdate', () => highlight(player.currentTime));
renderLog();
</script>
</body>
</html>"""


# ---- Pipeline Runner ----

def _run_pipeline(org_url: str, use_chrome: bool, max_per_section: int = 25, record_video: bool = False):
    """Run the full record-then-extract pipeline in a background thread."""
    from .guide import parse_markers, replace_markers
    from .recorder import GuideRecorder
    from .frame_selector import select_frames
    from playwright.sync_api import sync_playwright

    guide_path = Path(_current_job["guide_path"])
    output_path = Path(_current_job["output_path"])
    recording_dir = Path("/tmp/lab-screenshot-app/recording")
    video_dir = recording_dir / "video"

    if recording_dir.exists():
        shutil.rmtree(recording_dir)

    # Reset video state regardless — set below if record_video is on.
    _current_job["video_enabled"] = False
    _current_job["video_log_entries"] = []
    _current_job["video_start_time"] = None

    text = guide_path.read_text(encoding="utf-8")
    markers = parse_markers(text)

    log_progress(f"Loaded guide: {guide_path.name} ({len(markers)} markers)")
    log_progress(f"Per-section iteration cap: {max_per_section}")
    if record_video:
        video_dir.mkdir(parents=True, exist_ok=True)
        log_progress(f"Video recording: ON → {video_dir}")

    try:
        # ---- Setup: open browser for manual auth ----
        _current_job["status"] = "setup"
        log_progress("Opening browser — authenticate in all platforms you need.")
        log_progress("When ready, click 'Hand Off to Bot' in the app UI.")

        profile = os.path.expanduser("~/.okta-lab-screenshots/app-profile")
        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)

        chrome_kwargs = {"channel": "chrome"} if use_chrome else {}
        video_kwargs = {}
        if record_video:
            # Playwright records one webm per Page in the context. Files are flushed
            # on context.close(). Size matches the viewport for crisp playback.
            video_kwargs = {
                "record_video_dir": str(video_dir),
                "record_video_size": {"width": 1440, "height": 1080},
            }

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=False,
                viewport={"width": 1440, "height": 1080},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=1440,1200",  # Larger window to prevent content cutoff
                ],
                **chrome_kwargs,
                **video_kwargs,
            )
            if record_video:
                # Mark the t=0 reference for the viewer log alignment immediately
                # after the context (and therefore the video) is up.
                _current_job["video_start_time"] = time.time()
                _current_job["video_enabled"] = True
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(org_url, wait_until="networkidle", timeout=60000)

            # Wait for user to click "Hand Off to Bot" in the UI
            log_progress("Waiting for handoff signal...")
            while _current_job.get("status") == "setup":
                time.sleep(0.5)

            if _current_job.get("status") == "idle":
                # User cancelled
                context.close()
                return

            # ---- Pass 1: Record (same browser, same session) ----
            log_progress("Bot taking over — starting recording pass...")
            log_progress(f"Current page: {page.url}")

            # Use whichever page/tab is currently active
            # (user may have opened multiple tabs during auth)
            active_pages = context.pages
            if active_pages:
                page = active_pages[-1]  # Use the most recently opened tab
                for ap in active_pages:
                    if "/admin/" in ap.url or "/lab/" in ap.url:
                        page = ap
                        break
            log_progress(f"Active tab: {page.url} ({len(active_pages)} tabs open)")

            def _human_input_callback(question: str) -> str:
                """Ask the human operator via the web UI and wait for response."""
                _current_job["human_question"] = question
                _current_job["human_answer"] = None
                log_progress(f"🙋 BOT ASKS: {question}", "human")
                # Also prompt in terminal for convenience
                print(f"\n{'='*60}", file=sys.stderr)
                print(f"🙋 BOT ASKS: {question}", file=sys.stderr)
                print(f"   Respond in the web UI chat, or type here:", file=sys.stderr)
                print(f"{'='*60}", file=sys.stderr)

                # Start a thread to read from stdin (terminal) as backup
                import threading
                def _read_stdin():
                    try:
                        line = input().strip()
                        if line and _current_job.get("human_answer") is None:
                            _current_job["human_answer"] = line
                    except (EOFError, OSError):
                        pass
                stdin_thread = threading.Thread(target=_read_stdin, daemon=True)
                stdin_thread.start()

                # Wait for response from either web UI or terminal
                # Re-ping at 30s and 60s if no response
                timeout = 300  # 5 minutes
                waited = 0
                pinged = 0
                while _current_job.get("human_answer") is None and waited < timeout:
                    time.sleep(1)
                    waited += 1
                    if waited == 30 and pinged == 0:
                        pinged = 1
                        log_progress(f"🔔 REMINDER: The bot is still waiting for your response. Please check the chat panel or terminal.", "human")
                        print(f"\n🔔 REMINDER: Bot is still waiting for your response!", file=sys.stderr)
                    elif waited == 60 and pinged == 1:
                        pinged = 2
                        log_progress(f"🔔 FINAL REMINDER: The bot needs your input to continue. Respond in the chat or terminal.", "human")
                        print(f"\n🔔 FINAL REMINDER: Bot needs your input to continue!", file=sys.stderr)
                answer = _current_job.get("human_answer") or "No response (timed out)"
                _current_job["human_question"] = None
                log_progress(f"👤 HUMAN: {answer}", "human")
                return answer

            recorder = GuideRecorder(
                page=page,
                context=context,
                admin_url=org_url,
                output_dir=str(recording_dir),
                verbose=True,
                okta_api_key=_current_job.get("okta_api_key", ""),
                human_input_callback=_human_input_callback,
            )

            # Redirect recorder logs to our progress
            orig_log = recorder._log
            def patched_log(msg):
                orig_log(msg)
                log_progress(msg, "agent")
            recorder._log = patched_log

            recording = recorder.record_guide(text, max_per_section=max_per_section)
            context.close()

        log_progress(f"Pass 1 complete: {len(recording.frames)} frames captured")

        # ---- Video viewer bundle ----
        if record_video:
            try:
                _build_video_bundle(video_dir, recording_dir, _current_job["video_log_entries"])
                log_progress(f"Video bundle ready → recording-viewer.html ({len(_current_job['video_log_entries'])} log entries synced)")
            except Exception as e:
                log_progress(f"Video bundle generation failed: {e}", "error")

        # ---- Pass 2: Select ----
        _current_job["status"] = "selecting"
        log_progress("Pass 2: Selecting best frames via vision...")

        frames_meta = [
            {"index": f.index, "url": f.url, "title": f.title, "action": f.action, "png_path": f.png_path}
            for f in recording.frames
        ]

        # Redirect selector logs
        images = select_frames(frames_meta, markers, verbose=True)

        log_progress(f"Pass 2 complete: {len(images)}/{len(markers)} frames selected")

        # ---- Output ----
        updated = replace_markers(text, images)
        output_path.write_text(updated, encoding="utf-8")

        # Save individual PNGs
        screenshots_dir = Path("/tmp/lab-screenshot-app/screenshots")
        screenshots_dir.mkdir(exist_ok=True)
        screenshot_paths = {}
        for i, data_uri in images.items():
            png = base64.b64decode(data_uri.split(",")[1])
            png_path = screenshots_dir / f"screenshot-{i}.png"
            png_path.write_bytes(png)
            screenshot_paths[i] = str(png_path)

        _current_job["status"] = "done"
        _current_job["result"] = {
            "markers_total": len(markers),
            "markers_replaced": len(images),
            "output_path": str(output_path),
            "screenshots": screenshot_paths,
            "frames_captured": len(recording.frames),
            "video_available": record_video and (recording_dir / "recording-viewer.html").exists(),
        }
        log_progress(f"Done! {len(images)}/{len(markers)} markers replaced. Output: {output_path.name}")

    except Exception as e:
        _current_job["status"] = "error"
        log_progress(f"Error: {e}", "error")
        import traceback
        log_progress(traceback.format_exc(), "error")


# ---- Web UI ----

APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lab Screenshot</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f1f5f9; min-height: 100vh; }

.header { background: #1e293b; color: white; padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
.header .icon { font-size: 24px; }
.header h1 { font-size: 18px; font-weight: 600; }
.header p { font-size: 12px; color: #94a3b8; margin-left: auto; }

.container { max-width: 900px; margin: 32px auto; padding: 0 24px; }

.card { background: white; border-radius: 12px; border: 1px solid #e2e8f0; padding: 24px; margin-bottom: 20px; }
.card h2 { font-size: 16px; font-weight: 600; color: #1e293b; margin-bottom: 16px; }

.form-row { display: flex; gap: 16px; margin-bottom: 14px; }
.form-group { flex: 1; }
.form-group label { display: block; font-size: 13px; font-weight: 500; color: #475569; margin-bottom: 4px; }
.form-group input, .form-group select { width: 100%; padding: 8px 12px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 14px; }
.form-group input:focus, .form-group select:focus { outline: none; border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59,130,246,0.1); }

.upload-zone { border: 2px dashed #cbd5e1; border-radius: 8px; padding: 32px; text-align: center; cursor: pointer; transition: border-color 0.2s; }
.upload-zone:hover { border-color: #3b82f6; }
.upload-zone.has-file { border-color: #22c55e; border-style: solid; background: #f0fdf4; }
.upload-zone input { display: none; }
.upload-zone .label { font-size: 14px; color: #64748b; }
.upload-zone .filename { font-size: 15px; font-weight: 600; color: #166534; }

.markers { margin-top: 12px; }
.marker { display: flex; align-items: center; gap: 8px; padding: 6px 0; font-size: 13px; color: #475569; }
.marker .idx { background: #e2e8f0; color: #475569; border-radius: 4px; padding: 1px 6px; font-size: 11px; font-weight: 600; }

.checkbox-row { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.checkbox-row input[type=checkbox] { width: 16px; height: 16px; }
.checkbox-row label { font-size: 13px; color: #475569; }

.btn { padding: 10px 24px; border: none; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; }
.btn-primary { background: #3b82f6; color: white; }
.btn-primary:hover { background: #2563eb; }
.btn-primary:disabled { background: #94a3b8; cursor: not-allowed; }
.btn-secondary { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }
.btn-secondary:hover { background: #e2e8f0; }
.btn-row { display: flex; gap: 12px; margin-top: 8px; }

.status-bar { background: #1e293b; color: #94a3b8; border-radius: 8px; padding: 4px 0; margin-bottom: 20px; display: flex; align-items: center; }
.status-bar .step { flex: 1; text-align: center; padding: 8px; font-size: 12px; position: relative; }
.status-bar .step.active { color: #3b82f6; font-weight: 600; }
.status-bar .step.done { color: #22c55e; }

.log { background: #0f172a; border-radius: 8px; padding: 16px; max-height: 600px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.6; }
.log .entry { color: #94a3b8; word-wrap: break-word; overflow-wrap: break-word; white-space: pre-wrap; }
.log .entry.error { color: #ef4444; }
.log .entry.agent { color: #38bdf8; }
.log .entry.human { color: #f59e0b; font-weight: bold; }
.log .time { color: #475569; margin-right: 8px; }

.result { text-align: center; padding: 20px; }
.result .big { font-size: 48px; margin-bottom: 8px; }
.result .count { font-size: 24px; font-weight: 700; color: #1e293b; }
.result .sub { font-size: 14px; color: #64748b; margin-bottom: 20px; }

.screenshots { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-top: 16px; }
.screenshots img { width: 100%; border-radius: 6px; border: 1px solid #e2e8f0; }
.screenshots .caption { font-size: 11px; color: #64748b; margin-top: 4px; text-align: center; }
</style>
</head>
<body>
<div class="header">
    <span class="icon">📸</span>
    <h1>Lab Screenshot</h1>
    <p id="status-text">Ready</p>
</div>

<div class="container">
    <!-- User Guide (collapsible) -->
    <div class="card">
        <details>
            <summary style="cursor:pointer;font-weight:600;font-size:16px;color:#1e293b;">📖 How to Use This Tool</summary>
            <div style="margin-top:12px;font-size:14px;color:#475569;line-height:1.8;">
                <p><strong>Lab Screenshot Bot</strong> automates screenshot capture for Okta lab guides. Give it a markdown guide with <code>[SCREENSHOT: description]</code> markers, and it will open a browser, follow the guide steps, navigate the Okta Admin Console, and capture screenshots at each marker point. The output is a completed guide with real screenshots embedded.</p>

                <h3 style="color:#1e293b;margin-top:16px;">Getting Started</h3>
                <ol>
                    <li><strong>Upload your guide</strong> — Click "Choose File" and select a markdown (.md) file containing your lab guide. The guide should have <code>[SCREENSHOT: description]</code> markers where you want screenshots. The bot will show how many markers it found.</li>
                    <li><strong>Configure settings</strong> — Enter the starting URL for the lab, choose an AI model, and optionally provide an Okta API key (see Settings below).</li>
                    <li><strong>Start Recording</strong> — Click the button. A browser window will open automatically.</li>
                    <li><strong>Authenticate</strong> — In the browser window:
                        <ul style="margin-top:4px;">
                            <li>Log into the Okta org and any other platforms the lab requires</li>
                            <li>Click <strong>Launch</strong> on any platform buttons (Okta, virtual desktops, etc.)</li>
                            <li>Complete any MFA prompts</li>
                            <li>Navigate to where you want the bot to start working</li>
                        </ul>
                    </li>
                    <li><strong>Hand Off to Bot</strong> — When everything is ready, come back to this app and click the green button. The bot takes over the browser.</li>
                    <li><strong>Respond when asked</strong> — The bot may ask for your help via the chat panel (see below). You'll get a desktop notification and audible beep.</li>
                    <li><strong>Review & Download</strong> — When finished, download the output, preview it in the browser, or export a debug bundle for troubleshooting.</li>
                </ol>

                <h3 style="color:#1e293b;margin-top:16px;">Settings</h3>
                <ul>
                    <li><strong>Starting URL</strong> — The URL where the lab begins (e.g., <code>https://labs.demo.okta.com/lab/your-lab-id</code>).</li>
                    <li><strong>AI Model</strong> — Claude Sonnet 4.6 is recommended for the best balance of speed and accuracy.</li>
                    <li><strong>Okta API Key</strong> (optional but recommended) — An API token (SSWS format) for the target Okta org. This enables the bot to perform operations like enrolling MFA factors for users via API, which is required for labs involving MFA enrollment steps that would normally need a mobile device. Generate one in the Okta Admin Console under Security &gt; API &gt; Tokens.</li>
                    <li><strong>Max iterations per section</strong> — Caps how many tool calls (clicks, fills, scrolls, etc.) the bot may attempt per section before giving up or asking for help. Default 25 (range 15–100). Raise it (40–60) for guides with long forms, multi-page wizards, or complex policy edits where the bot needs more retries. Lower it (15–20) when iterating on guide development to fail fast.</li>
                    <li><strong>Use system Chrome</strong> — Check this if your organization's endpoint security blocks Playwright's bundled Chromium browser.</li>
                    <li><strong>Record video</strong> — Captures a webm video of the bot's browser session and packages it with a synced log into a self-contained HTML viewer. Off by default — enable it when you want a shareable artifact (demos, bug reports, training reviews). Files are sizeable (~50–200 MB per run). After the run, the <em>Download Video Bundle</em> button appears alongside the other download options.</li>
                </ul>

                <h3 style="color:#1e293b;margin-top:16px;">Bot Chat Panel</h3>
                <p>During recording, a chat panel appears below the progress log. This is where the bot communicates with you when it needs help. You can also type messages to provide guidance. Common scenarios:</p>
                <ul>
                    <li><strong>MFA push approval</strong> — The bot saved a security policy change and Okta is waiting for you to approve the push notification on your phone. Approve it, then type "done" or click Send.</li>
                    <li><strong>Navigation help</strong> — The bot tried multiple approaches and is stuck. Describe what you see or suggest what to click.</li>
                    <li><strong>Clarification</strong> — The bot needs more context about what the guide means or what the expected outcome looks like.</li>
                </ul>
                <p>When the bot needs help: the tab title will flash, you'll hear a beep, and a desktop notification will appear. <strong>Allow browser notifications when prompted</strong> so you don't miss these alerts.</p>

                <h3 style="color:#1e293b;margin-top:16px;">After the Run</h3>
                <ul>
                    <li><strong>Download Output</strong> — Saves the completed markdown with embedded screenshots</li>
                    <li><strong>Preview in Browser</strong> — Opens the output as a styled HTML page with rendered screenshots at <a href="/preview" target="_blank">/preview</a></li>
                    <li><strong>Download Frames</strong> — Zip of every PNG captured during navigation, plus <code>recording.json</code> metadata. Useful for picking a different screenshot than the one Pass 2 chose.</li>
                    <li><strong>Download Video Bundle</strong> (only shown when <em>Record video</em> was enabled) — Zip containing the session webm(s), a synced log JSONL, and <code>recording-viewer.html</code>. Open the HTML file in any browser to play the video alongside the console log; click any log row to seek the video to that moment.</li>
                    <li><strong>Export Debug Bundle</strong> — Downloads a zip containing the input guide, output, and full console log. Attach this to <a href="https://github.com/joevanhorn/lab-screenshot/issues" target="_blank">GitHub Issues</a> if you need to report a problem.</li>
                </ul>

                <h3 style="color:#1e293b;margin-top:16px;">Tips</h3>
                <ul>
                    <li>Allow browser notifications when first prompted — this is how the bot alerts you when it needs help</li>
                    <li>Keep your phone nearby for MFA push approvals during security policy changes</li>
                    <li>Don't interact with the bot's browser window while it's running — let it navigate on its own</li>
                    <li>For best results, make sure the Okta org is in a clean starting state (e.g., MFA policies not already configured)</li>
                    <li>If the bot gets stuck, check the chat panel — it may be waiting for your input</li>
                </ul>

                <h3 style="color:#1e293b;margin-top:16px;">Troubleshooting</h3>
                <ul>
                    <li><strong>Bot can't find an element</strong> — It will try multiple selectors automatically. If stuck, it asks for help via the chat panel.</li>
                    <li><strong>Screenshots look wrong</strong> — Make sure the browser window isn't minimized or covered during the run.</li>
                    <li><strong>API calls failing</strong> — Verify the Okta API key is correct and has Super Admin permissions.</li>
                    <li><strong>MFA prompt not appearing</strong> — Ensure Okta Verify is installed and push notifications are enabled on your device.</li>
                    <li><strong>Need to report a bug?</strong> — Click "Export Debug Bundle" and attach the zip to a <a href="https://github.com/joevanhorn/lab-screenshot/issues/new?template=bug_report.md" target="_blank">new issue</a>.</li>
                </ul>
            </div>
        </details>
    </div>
    <!-- Step indicators -->
    <div class="status-bar">
        <div class="step active" id="step-upload">1. Upload Guide</div>
        <div class="step" id="step-config">2. Configure</div>
        <div class="step" id="step-auth">3. Authenticate</div>
        <div class="step" id="step-record">4. Record</div>
        <div class="step" id="step-done">5. Done</div>
    </div>

    <!-- Upload -->
    <div class="card" id="card-upload">
        <h2>Upload Lab Guide</h2>
        <div class="upload-zone" id="upload-zone" onclick="document.getElementById('file-input').click()">
            <input type="file" id="file-input" accept=".md,.markdown,.txt" onchange="handleUpload(this)">
            <div class="label" id="upload-label">Click to upload a markdown guide with [SCREENSHOT: ...] markers</div>
        </div>
        <div class="markers" id="markers-list" style="display:none"></div>
    </div>

    <!-- Config -->
    <div class="card" id="card-config">
        <h2>Configuration</h2>
        <div class="form-row">
            <div class="form-group">
                <label>Starting URL</label>
                <input type="text" id="org-url" placeholder="https://labs.demo.okta.com/..." value="">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>AI Model</label>
                <select id="model">
                    <option value="claude-sonnet-4-6" selected>Claude Sonnet 4.6 (Recommended)</option>
                    <option value="claude-opus-4-6">Claude Opus 4.6</option>
                    <option value="claude-sonnet-4-5">Claude Sonnet 4.5</option>
                    <option value="claude-haiku-4-5">Claude Haiku 4.5 (Fastest)</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Okta API Key <span style="color:#94a3b8;font-weight:normal">(optional — enables admin API operations like factor enrollment)</span></label>
                <input type="password" id="okta-api-key" placeholder="SSWS 00abc..." autocomplete="off">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>Max iterations per section <span style="color:#94a3b8;font-weight:normal">(15–100, default 25; raise for long forms / complex policies)</span></label>
                <input type="number" id="max-per-section" min="15" max="100" step="1" value="25">
            </div>
        </div>
        <input type="hidden" id="llm-provider" value="litellm">
        <input type="hidden" id="api-key" value="sk-m4Lc0YlvjR0cjmDTR1qrJw">
        <input type="hidden" id="api-base" value="https://llm.atko.ai">
        <div class="checkbox-row">
            <input type="checkbox" id="use-chrome">
            <label for="use-chrome">Use system Chrome (avoids corporate endpoint blocks)</label>
        </div>
        <div class="checkbox-row">
            <input type="checkbox" id="record-video">
            <label for="record-video">Record video of the bot's session <span style="color:#94a3b8;font-weight:normal">(produces an HTML viewer with the video and console log side-by-side; ~50–200 MB per run)</span></label>
        </div>
        <div class="btn-row">
            <button class="btn btn-primary" id="start-btn" onclick="startRecording()" disabled>Start Recording</button>
        </div>
    </div>

    <!-- Handoff -->
    <div class="card" id="card-handoff" style="display:none">
        <h2>Authenticate & Prepare</h2>
        <p style="color:#475569;font-size:14px;margin-bottom:12px;">A browser window has opened. Complete these steps:</p>
        <ol style="color:#475569;font-size:14px;line-height:2;padding-left:20px;margin-bottom:16px;">
            <li>Log into the lab environment</li>
            <li>Click <strong>Launch</strong> on any platform buttons (Okta, virtual desktops, etc.)</li>
            <li>Complete any MFA prompts</li>
            <li>Navigate to where you want the bot to start working</li>
        </ol>
        <p style="color:#64748b;font-size:13px;margin-bottom:16px;">When you're ready, click the button below. The bot will take over the browser and start following the guide instructions.</p>
        <button class="btn btn-primary" onclick="handOffToBot()" style="background:#22c55e;font-size:16px;padding:12px 32px;">Hand Off to Bot</button>
    </div>

    <!-- Progress -->
    <div class="card" id="card-progress" style="display:none">
        <h2>Progress</h2>
        <div class="log" id="log"></div>
    </div>

    <!-- Bot Chat / Human Input Panel -->
    <div class="card" id="card-chat" style="display:none; border: 2px solid #3b82f6;">
        <h2>💬 Bot Chat</h2>
        <div id="chat-messages" style="max-height:300px;overflow-y:auto;margin-bottom:12px;padding:8px;background:#f8fafc;border-radius:6px;font-size:14px;"></div>
        <div id="chat-input-row" style="display:flex;gap:8px;">
            <input type="text" id="chat-input" placeholder="Type a message to the bot..." style="flex:1;padding:8px 12px;border:1px solid #e2e8f0;border-radius:6px;font-size:14px;" onkeydown="if(event.key==='Enter')sendHumanResponse()">
            <button class="btn btn-primary" onclick="sendHumanResponse()">Send</button>
        </div>
        <div id="chat-waiting" style="display:none;padding:8px;color:#b45309;font-size:13px;">⏳ Waiting for your response...</div>
    </div>

    <!-- Result -->
    <div class="card" id="card-result" style="display:none">
        <h2>Results</h2>
        <div class="result">
            <div class="big">✅</div>
            <div class="count" id="result-count"></div>
            <div class="sub" id="result-sub"></div>
            <div class="btn-row" style="justify-content:center">
                <button class="btn btn-primary" onclick="downloadOutput()">Download Output</button>
                <a href="/preview" target="_blank" class="btn btn-secondary" style="text-decoration:none;">Preview in Browser</a>
                <a href="/api/download-recording" class="btn btn-secondary" style="text-decoration:none;" title="Downloads all captured frame screenshots and metadata as a zip">Download Frames</a>
                <a id="download-video-btn" href="/api/download-video" class="btn btn-secondary" style="text-decoration:none;display:none;" title="Downloads the session video, synced log, and HTML viewer as a zip. Open recording-viewer.html to play the video with the console log side-by-side.">Download Video Bundle</a>
                <a href="/api/debug-bundle" class="btn btn-secondary" style="text-decoration:none;" title="Downloads input guide, output, and console logs as a zip — useful for bug reports">Export Debug Bundle</a>
                <button class="btn btn-secondary" onclick="resetApp()">New Guide</button>
            </div>
        </div>
        <div class="screenshots" id="screenshots"></div>
    </div>
</div>

<script>
let guideUploaded = false;
let ws;

function connectWS() {
    ws = new WebSocket('ws://' + location.host + '/ws');
    ws.onmessage = function(e) {
        const msg = JSON.parse(e.data);
        if (msg.type === 'progress') {
            addLogEntry(msg);
            // Show chat panel when recording starts
            if (msg.message && (msg.message.includes('Bot taking over') || msg.message.includes('recording pass'))) {
                document.getElementById('card-chat').style.display = 'block';
            }
            // Bot asking for help
            if (msg.level === 'human' && msg.message.startsWith('🙋')) {
                const question = msg.message.replace('🙋 BOT ASKS: ', '');
                addChatMessage('bot', question);
                document.getElementById('chat-waiting').style.display = 'block';
                document.getElementById('chat-input').focus();
                // Desktop notification
                if (Notification.permission === 'granted') {
                    new Notification('🙋 Lab Screenshot Bot needs you!', {
                        body: question.substring(0, 120),
                        requireInteraction: true,
                        tag: 'bot-help'
                    });
                }
                // Flash tab title
                let originalTitle = document.title;
                window._titleFlash = setInterval(() => {
                    document.title = document.title === '🙋 BOT NEEDS HELP' ? originalTitle : '🙋 BOT NEEDS HELP';
                }, 1000);
                // Audible beep via Web Audio API
                try {
                    const ctx = new (window.AudioContext || window.webkitAudioContext)();
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.frequency.value = 800;
                    gain.gain.value = 0.3;
                    osc.start();
                    osc.stop(ctx.currentTime + 0.3);
                } catch(e) {}
            }
            // Human response logged
            if (msg.level === 'human' && msg.message.startsWith('👤')) {
                document.getElementById('chat-waiting').style.display = 'none';
            }
        }
    };
    ws.onclose = function() { setTimeout(connectWS, 2000); };
}
connectWS();

function addLogEntry(entry) {
    const log = document.getElementById('log');
    const div = document.createElement('div');
    div.className = 'entry ' + (entry.level || 'info');
    div.innerHTML = '<span class="time">' + entry.time + '</span>' + escapeHtml(entry.message);
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
}

function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addChatMessage(sender, text) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.style.marginBottom = '8px';
    div.style.padding = '8px 12px';
    div.style.borderRadius = '8px';
    if (sender === 'bot') {
        div.style.background = '#fffbeb';
        div.style.borderLeft = '3px solid #f59e0b';
        div.innerHTML = '<strong style="color:#b45309;">🤖 Bot:</strong> ' + escapeHtml(text);
    } else {
        div.style.background = '#eff6ff';
        div.style.borderLeft = '3px solid #3b82f6';
        div.innerHTML = '<strong style="color:#1d4ed8;">👤 You:</strong> ' + escapeHtml(text);
    }
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function sendHumanResponse() {
    const input = document.getElementById('chat-input');
    const answer = input.value.trim() || 'Continue';
    addChatMessage('human', answer);
    input.value = '';
    document.getElementById('chat-waiting').style.display = 'none';
    const form = new FormData();
    form.append('answer', answer);
    await fetch('/api/human-response', { method: 'POST', body: form });
    // Stop title flash
    if (window._titleFlash) { clearInterval(window._titleFlash); document.title = 'Lab Screenshot'; }
}

// Request notification permission on load
if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
}

async function handleUpload(input) {
    const file = input.files[0];
    if (!file) return;

    const form = new FormData();
    form.append('file', file);

    const resp = await fetch('/api/upload-guide', { method: 'POST', body: form });
    const data = await resp.json();

    document.getElementById('upload-zone').classList.add('has-file');
    document.getElementById('upload-label').innerHTML = '<div class="filename">' + data.filename + '</div><div class="label">' + data.markers.length + ' screenshot markers found</div>';

    const ml = document.getElementById('markers-list');
    ml.style.display = 'block';
    ml.innerHTML = data.markers.map(m =>
        '<div class="marker"><span class="idx">' + m.index + '</span>' + escapeHtml(m.description) + '</div>'
    ).join('');

    guideUploaded = true;
    document.getElementById('start-btn').disabled = false;
    setStep('config');
}

function toggleProviderFields() {
    // Provider fields are hidden — using built-in LiteLLM proxy
}

async function startRecording() {
    if (!guideUploaded) return;

    // Validate required fields
    const orgUrl = document.getElementById('org-url').value.trim();
    if (!orgUrl) {
        document.getElementById('org-url').style.borderColor = '#ef4444';
        document.getElementById('org-url').focus();
        alert('Please enter a Starting URL');
        return;
    }
    document.getElementById('org-url').style.borderColor = '';

    document.getElementById('card-progress').style.display = 'block';
    document.getElementById('card-result').style.display = 'none';
    document.getElementById('log').innerHTML = '';
    document.getElementById('start-btn').disabled = true;

    setStep('auth');
    document.getElementById('status-text').textContent = 'Opening browser...';
    document.getElementById('card-handoff').style.display = 'block';

    const form = new FormData();
    form.append('org_url', document.getElementById('org-url').value);
    form.append('llm_provider', document.getElementById('llm-provider').value);
    form.append('api_key', document.getElementById('api-key').value);
    form.append('api_base', document.getElementById('api-base').value);
    form.append('model', document.getElementById('model').value);
    form.append('use_chrome', document.getElementById('use-chrome').checked);
    form.append('okta_api_key', document.getElementById('okta-api-key').value);
    form.append('max_per_section', document.getElementById('max-per-section').value);
    form.append('record_video', document.getElementById('record-video').checked);

    await fetch('/api/start', { method: 'POST', body: form });

    // Poll for status
    pollStatus();
}

async function handOffToBot() {
    const resp = await fetch('/api/handoff', { method: 'POST' });
    if (resp.ok) {
        document.getElementById('card-handoff').style.display = 'none';
        document.getElementById('card-progress').style.display = 'block';
        document.getElementById('card-chat').style.display = 'block';
        document.getElementById('status-text').textContent = 'Bot is recording...';
        setStep('record');
    }
}

async function pollStatus() {
    const resp = await fetch('/api/status');
    const data = await resp.json();

    document.getElementById('status-text').textContent = data.status;

    if (data.status === 'setup') {
        setStep('auth');
        document.getElementById('card-handoff').style.display = 'block';
    } else if (data.status === 'recording') {
        setStep('record');
        document.getElementById('card-handoff').style.display = 'none';
        document.getElementById('card-progress').style.display = 'block';
    } else if (data.status === 'selecting') {
        setStep('record');
        document.getElementById('status-text').textContent = 'Selecting best frames...';
    } else if (data.status === 'done') {
        setStep('done');
        showResult(data.result);
        document.getElementById('start-btn').disabled = false;
        return;
    } else if (data.status === 'error') {
        document.getElementById('status-text').textContent = 'Error';
        document.getElementById('start-btn').disabled = false;
        return;
    }

    setTimeout(pollStatus, 2000);
}

function showResult(result) {
    document.getElementById('card-result').style.display = 'block';
    document.getElementById('result-count').textContent = result.markers_replaced + '/' + result.markers_total + ' screenshots captured';
    document.getElementById('result-sub').textContent = result.frames_captured + ' frames recorded during Pass 1';
    document.getElementById('status-text').textContent = 'Done!';
    // Show the video bundle button only if a video was actually recorded.
    const vbtn = document.getElementById('download-video-btn');
    if (vbtn) vbtn.style.display = result.video_available ? '' : 'none';
}

async function downloadOutput() {
    window.location.href = '/api/download';
}

function resetApp() {
    guideUploaded = false;
    document.getElementById('upload-zone').classList.remove('has-file');
    document.getElementById('upload-label').textContent = 'Click to upload a markdown guide with [SCREENSHOT: ...] markers';
    document.getElementById('markers-list').style.display = 'none';
    document.getElementById('card-handoff').style.display = 'none';
    document.getElementById('card-progress').style.display = 'none';
    document.getElementById('card-chat').style.display = 'none';
    document.getElementById('chat-messages').innerHTML = '';
    document.getElementById('card-result').style.display = 'none';
    document.getElementById('start-btn').disabled = true;
    document.getElementById('log').innerHTML = '';
    setStep('upload');
    document.getElementById('status-text').textContent = 'Ready';
}

function setStep(step) {
    const steps = ['upload', 'config', 'auth', 'record', 'done'];
    const current = steps.indexOf(step);
    steps.forEach((s, i) => {
        const el = document.getElementById('step-' + s);
        el.className = 'step' + (i < current ? ' done' : i === current ? ' active' : '');
    });
}

toggleProviderFields();
</script>
</body>
</html>"""


def run_app(port: int = 8384):
    """Start the app server and open browser."""
    print(f"Lab Screenshot running at http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
