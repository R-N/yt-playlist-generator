"""
FastAPI backend for the match-review app.

Safety posture:
  - Audio endpoint is strictly READ-ONLY: it serves files from MP3_FOLDERS and
    nothing else. No endpoint deletes or writes any audio file.
  - Curation writes go through db.py (append-only decisions + atomic export).
"""
import os
import re
import shutil
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import jobs
import settings
import discord_service
from config import MP3_FOLDERS, AUTO_EXPORT_EVERY, REPO_ROOT

if REPO_ROOT not in sys.path:            # reuse the repo-root playlist logic (no duplication)
    sys.path.insert(0, REPO_ROOT)
import playlist_generator                # noqa: E402

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
    settings.apply_to_environ()     # load .env secrets so subprocesses inherit them
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


class PlaylistReq(BaseModel):
    text: str = ""        # pasted URLs/ids, one per line


@app.get("/api/counts")
def api_counts():
    return db.counts()


@app.post("/api/playlists")
def api_playlists(req: PlaylistReq):
    """Turn pasted YouTube URLs/ids into watch_videos playlist URLs (the original
    playlist_generator, in the browser). Chunks ids in groups of 50."""
    ids = playlist_generator.extract_ids(req.text.splitlines())
    return {"id_count": len(ids), "playlists": playlist_generator.build_playlists(ids)}


@app.get("/api/rows")
def api_rows(status: str = "all", limit: int = 200, offset: int = 0):
    rows, total = db.get_rows(status=status, limit=limit, offset=offset)
    for r in rows:
        r["has_local"] = r["filename"] in _FILE_INDEX
    return {"rows": rows, "total": total}


def _track_state(r, has_local):
    """One digestible state per track, driving the Library list's colored chip.
    check wins (a human decided); otherwise describe what the row has."""
    if r.get("check") == 1:
        return "confirmed"
    if r.get("check") == 0:
        return "rejected"
    has_link = bool(r.get("yt_id"))
    if has_local and has_link:
        return "unreviewed"
    if has_local:
        return "file_only"
    if has_link:
        return "link_only"
    return "new"


# Slim fields the library list needs — keeps the all-rows payload small.
_LIB_FIELDS = ("id", "artist", "title", "filename", "yt_id", "yt_channel",
               "yt_title", "duration", "check")


@app.get("/api/library")
def api_library():
    """All tracks, trimmed + tagged with a state, for the browse/list view."""
    rows, total = db.get_rows(status="all", limit=1_000_000, offset=0)
    out = []
    for r in rows:
        has_local = r["filename"] in _FILE_INDEX
        item = {k: r.get(k) for k in _LIB_FIELDS}
        item["has_local"] = has_local
        item["state"] = _track_state(r, has_local)
        out.append(item)
    return {"rows": out, "total": total}


@app.get("/api/track/{track_id}")
def api_track(track_id: int):
    """Full expanded row for one track (mb_*, sims, everything) — used when the
    Library list hands a row off to the Review view."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    r = db._expand_extra(dict(row))
    r["has_local"] = r["filename"] in _FILE_INDEX
    return r


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


# --- Settings (.env secrets) ------------------------------------------------
class SettingsIn(BaseModel):
    DISCORD_BOT_TOKEN: str | None = None
    DISCORD_CHANNEL_ID: str | None = None
    ACOUSTID_API_KEY: str | None = None


@app.get("/api/settings")
def api_settings_get():
    return settings.public_view()


@app.post("/api/settings")
def api_settings_set(s: SettingsIn):
    # only persist keys the client actually sent (others stay as-is)
    sent = {k: v for k, v in s.model_dump().items() if v is not None}
    return settings.save(sent)


# --- Discord harvest --------------------------------------------------------
class DiscordFetchIn(BaseModel):
    channel_id: str | None = None
    author: str | None = None
    write_files: bool = True


@app.post("/api/discord/fetch")
def api_discord_fetch(d: DiscordFetchIn):
    channel_id = d.channel_id or settings.get("DISCORD_CHANNEL_ID")
    try:
        return discord_service.fetch_and_extract(
            channel_id, author=d.author, write_files=d.write_files
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Likes queue (consumed by the Chrome liker extension) -------------------
@app.get("/api/likes/queue")
def api_likes_queue():
    """Serve the harvested video ids (ids.txt) for the liker extension to like."""
    path = os.path.join(REPO_ROOT, "ids.txt")
    ids = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            ids = [line.strip() for line in f if line.strip()]
    return {"ids": ids}


# --- Pipeline scripts (background jobs) -------------------------------------
class JobIn(BaseModel):
    args: list[str] = []


@app.get("/api/scripts")
def api_scripts():
    return jobs.catalog()


@app.get("/api/scripts/{name}")
def api_script_state(name: str, tail: int | None = None):
    if name not in jobs.SCRIPTS:
        raise HTTPException(status_code=404, detail="unknown script")
    return jobs.state(name, tail=tail)


@app.post("/api/scripts/{name}/run")
def api_script_run(name: str, body: JobIn | None = None):
    try:
        return jobs.start(name, args=(body.args if body else None))
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown script")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/scripts/{name}/stop")
def api_script_stop(name: str):
    if name not in jobs.SCRIPTS:
        raise HTTPException(status_code=404, detail="unknown script")
    return jobs.stop(name)


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


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _resolve_yt_audio(yt_id):
    """Direct audio-stream URL for a YouTube id via `yt-dlp -g`, or None. Network.
    Pulled out as its own function so tests can stub it without hitting yt-dlp."""
    yt = shutil.which("yt-dlp")
    cmd = [yt] if yt else [sys.executable, "-m", "yt_dlp"]
    cmd += ["-g", "-f", "bestaudio/best", "--no-playlist",
            f"https://www.youtube.com/watch?v={yt_id}"]
    cookies = os.path.join(REPO_ROOT, "cookies.txt")   # reuse the repo's exported cookies
    if os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else None


@app.get("/api/yt_audio/{yt_id}")
def api_yt_audio(yt_id: str):
    """Resolve a YouTube candidate's audio stream and redirect the <audio> element to
    it, so a reviewer can verify the match by ear -- works even when the IFrame embed
    is blocked (age-restricted / embedding disabled). Read-only; touches no files."""
    if not _YT_ID_RE.match(yt_id):
        raise HTTPException(status_code=400, detail="invalid youtube id")
    url = _resolve_yt_audio(yt_id)
    if not url:
        raise HTTPException(status_code=502, detail="could not resolve audio (yt-dlp / network / age-gate)")
    return RedirectResponse(url)


# Serve the built SPA if present (frontend/dist). Mount LAST so /api/* wins.
_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="spa")
