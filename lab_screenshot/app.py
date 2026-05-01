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

# Global state for the current job
_current_job = {
    "status": "idle",  # idle, setup, recording, selecting, done, error
    "progress": [],
    "result": None,
    "guide_path": None,
    "output_path": None,
}

_websocket_clients: list[WebSocket] = []


async def broadcast(msg: dict):
    """Send a message to all connected WebSocket clients."""
    for ws in _websocket_clients[:]:
        try:
            await ws.send_json(msg)
        except Exception:
            _websocket_clients.remove(ws)


def log_progress(message: str, level: str = "info"):
    """Log a progress message and broadcast to UI."""
    entry = {"time": time.strftime("%H:%M:%S"), "level": level, "message": message}
    _current_job["progress"].append(entry)
    # Broadcast async from sync context
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast({"type": "progress", **entry}))
    except RuntimeError:
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

    # Reset state
    _current_job["status"] = "setup"
    _current_job["progress"] = []
    _current_job["result"] = None

    # Run in background thread
    thread = threading.Thread(
        target=_run_pipeline,
        args=(org_url, use_chrome),
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


@app.get("/api/download")
async def download_output():
    """Download the output markdown file."""
    path = _current_job.get("output_path")
    if path and Path(path).exists():
        return FileResponse(path, filename=Path(path).name, media_type="text/markdown")
    return JSONResponse({"error": "No output available"}, status_code=404)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _websocket_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _websocket_clients.remove(ws)


# ---- Pipeline Runner ----

def _run_pipeline(org_url: str, use_chrome: bool):
    """Run the full record-then-extract pipeline in a background thread."""
    from .guide import parse_markers, replace_markers
    from .recorder import GuideRecorder
    from .frame_selector import select_frames
    from playwright.sync_api import sync_playwright

    guide_path = Path(_current_job["guide_path"])
    output_path = Path(_current_job["output_path"])
    recording_dir = Path("/tmp/lab-screenshot-app/recording")

    if recording_dir.exists():
        shutil.rmtree(recording_dir)

    text = guide_path.read_text(encoding="utf-8")
    markers = parse_markers(text)

    log_progress(f"Loaded guide: {guide_path.name} ({len(markers)} markers)")

    try:
        # ---- Setup: open browser for manual auth ----
        _current_job["status"] = "setup"
        log_progress("Opening browser — authenticate in all platforms you need.")
        log_progress("When ready, click 'Hand Off to Bot' in the app UI.")

        profile = os.path.expanduser("~/.okta-lab-screenshots/app-profile")
        shutil.rmtree(profile, ignore_errors=True)
        os.makedirs(profile, exist_ok=True)

        chrome_kwargs = {"channel": "chrome"} if use_chrome else {}

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                headless=False,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
                **chrome_kwargs,
            )
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

            recorder = GuideRecorder(
                page=page,
                context=context,
                admin_url=org_url,
                output_dir=str(recording_dir),
                verbose=True,
            )

            # Redirect recorder logs to our progress
            orig_log = recorder._log
            def patched_log(msg):
                orig_log(msg)
                log_progress(msg, "agent")
            recorder._log = patched_log

            recording = recorder.record_guide(text)
            context.close()

        log_progress(f"Pass 1 complete: {len(recording.frames)} frames captured")

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

.log { background: #0f172a; border-radius: 8px; padding: 16px; max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.6; }
.log .entry { color: #94a3b8; }
.log .entry.error { color: #ef4444; }
.log .entry.agent { color: #38bdf8; }
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
        <input type="hidden" id="llm-provider" value="litellm">
        <input type="hidden" id="api-key" value="sk-m4Lc0YlvjR0cjmDTR1qrJw">
        <input type="hidden" id="api-base" value="https://llm.atko.ai">
        <div class="checkbox-row">
            <input type="checkbox" id="use-chrome">
            <label for="use-chrome">Use system Chrome (avoids corporate endpoint blocks)</label>
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

    <!-- Result -->
    <div class="card" id="card-result" style="display:none">
        <h2>Results</h2>
        <div class="result">
            <div class="big">✅</div>
            <div class="count" id="result-count"></div>
            <div class="sub" id="result-sub"></div>
            <div class="btn-row" style="justify-content:center">
                <button class="btn btn-primary" onclick="downloadOutput()">Download Output</button>
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
        if (msg.type === 'progress') addLogEntry(msg);
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

    await fetch('/api/start', { method: 'POST', body: form });

    // Poll for status
    pollStatus();
}

async function handOffToBot() {
    const resp = await fetch('/api/handoff', { method: 'POST' });
    if (resp.ok) {
        document.getElementById('card-handoff').style.display = 'none';
        document.getElementById('card-progress').style.display = 'block';
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
