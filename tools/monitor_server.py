#!/usr/bin/env python3
"""
Kwork Scout Monitor — FastAPI backend + SSE real-time updates.

Запуск:
  python3 tools/monitor_server.py
  → http://localhost:8080

Зависимости: pip install fastapi uvicorn jinja2 (уже в requirements.txt)
"""
import asyncio
import hashlib
import json
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --- lazy import (not breaking if deps missing) ---
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    from fastapi.templating import Jinja2Templates
    import uvicorn
except ImportError:
    print("[ERROR] Missing dependencies. Run:")
    print("  pip install fastapi uvicorn jinja2")
    sys.exit(1)

# ============================================================
# Paths
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE  = PROJECT_ROOT / "logs" / "pipeline_status.json"
RUNS_DIR     = PROJECT_ROOT / "logs" / "runs"
TEMPLATE_DIR = PROJECT_ROOT / "templates"

# ============================================================
# FastAPI app
# ============================================================
app = FastAPI(title="Kwork Scout Monitor", version="1.0.0")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# ============================================================
# Helpers
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path: Path) -> str:
    """Fast content hash for change detection."""
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _read_status() -> dict:
    """Read pipeline_status.json safely."""
    if not STATUS_FILE.exists():
        return {
            "run_id": None,
            "steps": [],
            "aggregated": {},
            "projects": [],
            "errors": [],
            "_dry_run": True,
            "started_at": None,
            "categories": [],
        }
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "run_id": None,
            "steps": [],
            "aggregated": {},
            "projects": [],
            "errors": [],
            "_dry_run": True,
            "started_at": None,
            "categories": [],
        }


def _list_runs() -> list[dict]:
    """List recent runs from logs/runs/*.json (newest first, max 8)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    runs = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True)[:8]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            runs.append({
                "run_id": data.get("run_id", f.stem),
                "started_at": data.get("started_at"),
                "ok_steps": data.get("ok_steps"),
                "total_steps": data.get("total_steps"),
                "total_duration_sec": data.get("total_duration_sec"),
                "history_count": data.get("history_count"),
                "rejected_count": data.get("rejected_count"),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return runs


# ============================================================
# Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "monitor.html")


@app.get("/api/status")
async def api_status():
    return JSONResponse(content=_read_status())


@app.get("/api/history")
async def api_history():
    return JSONResponse(content=_list_runs())


@app.get("/api/runs/{run_id}")
async def api_run(run_id: str):
    run_file = RUNS_DIR / f"{run_id}.json"
    if not run_file.exists():
        return JSONResponse(status_code=404, content={"error": "Run not found"})
    try:
        data = json.loads(run_file.read_text(encoding="utf-8"))
        return JSONResponse(content=data)
    except (json.JSONDecodeError, OSError):
        return JSONResponse(status_code=500, content={"error": "Failed to read run file"})


PROPOSALS_DIR = Path(__file__).resolve().parent.parent / "proposals"


@app.get("/api/proposal/{filename}")
async def api_proposal(filename: str):
    """Serve a proposal markdown file from proposals/ for download."""
    # Security: prevent directory traversal
    if ".." in filename or "/" in filename:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    prop_file = PROPOSALS_DIR / filename
    if not prop_file.exists() or not prop_file.is_file():
        return JSONResponse(status_code=404, content={"error": "Proposal not found"})
    try:
        content = prop_file.read_text(encoding="utf-8")
        from fastapi.responses import Response
        return Response(
            content=content,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    except OSError:
        return JSONResponse(status_code=500, content={"error": "Failed to read proposal"})


@app.get("/api/events")
async def api_events():
    """Server-Sent Events endpoint. Pushes status on change."""
    last_hash = ""

    async def event_generator():
        nonlocal last_hash
        try:
            while True:
                current_hash = _hash_file(STATUS_FILE)
                if current_hash and current_hash != last_hash:
                    last_hash = current_hash
                    status = _read_status()
                    yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            # Server is shutting down — exit cleanly, no traceback
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Main
# ============================================================

def main():
    host = "0.0.0.0"
    port = 8080

    # Auto-create dirs
    (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # Write initial empty status if not present
    if not STATUS_FILE.exists():
        STATUS_FILE.write_text(json.dumps({
            "run_id": None,
            "steps": [],
            "aggregated": {},
            "projects": [],
            "errors": [],
            "_dry_run": True,
            "started_at": None,
            "categories": [],
            "_monitor_started": _now_iso(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Auto-open browser
    def open_browser():
        time.sleep(1.5)
        webbrowser.open(f"http://localhost:{port}")

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    print(f"[Monitor] Starting on http://localhost:{port}")
    print(f"[Monitor] Status file: {STATUS_FILE}")
    print(f"[Monitor] Runs dir: {RUNS_DIR}")
    print(f"[Monitor] Press Ctrl+C to stop")

    uvicorn.run(app, host=host, port=port, log_level="info",
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
