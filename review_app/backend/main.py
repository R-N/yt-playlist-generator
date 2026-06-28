"""
FastAPI backend for the match-review app.

Safety posture:
  - Audio endpoint is strictly READ-ONLY: it serves files from MP3_FOLDERS and
    nothing else. No endpoint deletes or writes any audio file.
  - Curation writes go through db.py (append-only decisions + atomic export).
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import MP3_FOLDERS, AUTO_EXPORT_EVERY

app = FastAPI(title="Match Review")

# Dev: Vite serves the SPA on :5173 and calls the API on :8000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# filename -> absolute path, built once at startup. Read-only lookup.
_FILE_INDEX = {}
_decision_count = 0     # since-startup counter for auto-export


@app.on_event("startup")
def _startup():
    db.init_db()
    for folder in MP3_FOLDERS:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".mp3") and name not in _FILE_INDEX:
                _FILE_INDEX[name] = os.path.join(folder, name)
    print(f"Indexed {len(_FILE_INDEX)} local mp3 files")


class Decision(BaseModel):
    track_id: int
    decision: bool        # True = approve, False = reject


@app.get("/api/counts")
def api_counts():
    return db.counts()


@app.get("/api/rows")
def api_rows(status: str = "all", limit: int = 200, offset: int = 0):
    rows, total = db.get_rows(status=status, limit=limit, offset=offset)
    for r in rows:
        r["has_local"] = r["filename"] in _FILE_INDEX
    return {"rows": rows, "total": total}


@app.post("/api/decision")
def api_decision(d: Decision):
    global _decision_count
    try:
        result = db.record_decision(d.track_id, d.decision)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _decision_count += 1
    if AUTO_EXPORT_EVERY and _decision_count % AUTO_EXPORT_EVERY == 0:
        db.export_csv_only()       # atomic; marks flow to the git-tracked CSV
        result["auto_exported"] = True
    return result


@app.post("/api/export")
def api_export():
    return db.export_matches()


@app.get("/api/audio/{track_id}")
def api_audio(track_id: int):
    """Serve the local mp3 read-only. FileResponse handles HTTP Range
    automatically, so the player can seek/scrub."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT filename FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    path = _FILE_INDEX.get(row["filename"])
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="local file not found")
    return FileResponse(path, media_type="audio/mpeg")


# Serve the built SPA if present (frontend/dist). Mount LAST so /api/* wins.
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="spa")
