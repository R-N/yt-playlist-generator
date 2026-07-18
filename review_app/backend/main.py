"""
FastAPI backend for the match-review app.

Safety posture:
  - Audio endpoint is strictly READ-ONLY: it serves files from MP3_FOLDERS and
    nothing else. No endpoint deletes or writes any audio file.
  - Curation writes go through db.py (append-only decisions + atomic export).
"""
import os
import csv
import importlib.util
import io
import json
import re
import shutil
import stat
import subprocess
import sqlite3
import sys
import uuid
import secrets
import time
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Mapping

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import jobs
import tasks
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

_AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg",
    ".ogg": "audio/ogg", ".flac": "audio/flac",
}


@dataclass(frozen=True)
class FileCatalog:
    records: tuple
    by_basename: Mapping


_CATALOG = FileCatalog((), MappingProxyType({}))
_decision_count = 0     # since-startup counter for auto-export
RUN_STORAGE = os.path.join(os.path.dirname(__file__), "workspace_runs")
_DELETE_TOKENS = {}
_DELETE_TOKEN_TTL = 60

# The downloader names files `... [<id>].ext`; this pulls the id back out.
_DOWNLOAD_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")


def _download_id(name):
    match = _DOWNLOAD_ID_RE.search(name)
    return match.group(1) if match else None


@app.on_event("startup")
def _startup():
    settings.apply_to_environ()     # load .env secrets so subprocesses inherit them
    db.init_db()
    _cleanup_workspace_run_files()
    folders = settings.configured_mp3_folders()
    _install_catalog(_build_file_catalog(folders))
    print(f"Indexed {len(_CATALOG.records)} local audio files")


def _run_path(path):
    if not path:
        return None
    storage = os.path.realpath(RUN_STORAGE)
    candidate = os.path.realpath(path)
    try:
        if candidate == storage or os.path.commonpath((storage, candidate)) != storage:
            return None
    except ValueError:
        return None
    return candidate if os.path.isfile(candidate) else None


def _cleanup_workspace_run_files():
    for run in db.list_workspace_runs():
        path = _run_path(run.get("input_path"))
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def _build_file_catalog(folders):
    records = []
    physical = set()
    for folder in folders:
        folder_identity = os.path.normcase(os.path.realpath(folder))
        if not os.path.isdir(folder_identity):
            raise ValueError(f"MP3 folder does not exist: {folder}")
        for root, dirs, files in os.walk(folder_identity, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in files:
                path = os.path.join(root, name)
                if os.path.splitext(name)[1].lower() not in _AUDIO_MEDIA_TYPES:
                    continue
                try:
                    is_link = _is_reparse_point(path)
                except OSError:
                    continue
                if is_link:
                    continue
                resolved = os.path.realpath(path)
                try:
                    if os.path.commonpath((folder_identity, resolved)) != folder_identity:
                        continue
                except ValueError:
                    continue
                try:
                    info = os.stat(resolved)
                    identity = ((info.st_dev, info.st_ino) if info.st_ino else
                                ("path", os.path.normcase(resolved)))
                except OSError:
                    continue
                if identity in physical:
                    continue
                physical.add(identity)
                relative = os.path.relpath(path, folder_identity).replace(os.sep, "/")
                records.append({
                    "folder_identity": folder_identity,
                    "relative_path": relative,
                    "path": resolved,
                    "basename": name,
                    "file_size": info.st_size,
                    "modified_at": str(info.st_mtime_ns),
                    "media_type": _AUDIO_MEDIA_TYPES[os.path.splitext(name)[1].lower()],
                })
    frozen = tuple(MappingProxyType(record) for record in records)
    index = {}
    for record in frozen:
        index.setdefault(os.path.normcase(record["basename"]), []).append(record)
    return FileCatalog(frozen, MappingProxyType({k: tuple(v) for k, v in index.items()}))


def _is_reparse_point(path):
    if os.path.islink(path):
        return True
    attrs = getattr(os.stat(path), "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _swap_file_catalog(records):
    global _CATALOG
    _CATALOG = records


def _install_catalog(records):
    db.sync_catalog_links(records.records)
    _swap_file_catalog(records)


def _record_for_filename(filename):
    catalog = _CATALOG
    candidates = catalog.by_basename.get(os.path.normcase(filename), ())
    if len(candidates) != 1:
        return None
    record = candidates[0]
    path = record["path"]
    if not os.path.isfile(path):
        return None
    try:
        if os.path.commonpath((record["folder_identity"], os.path.realpath(path))) != record["folder_identity"]:
            return None
    except ValueError:
        return None
    return record


def _has_local(filename):
    return _record_for_filename(filename) is not None


class Decision(BaseModel):
    track_id: int
    decision: bool        # True = approve, False = reject


class PlaylistReq(BaseModel):
    text: str = ""        # pasted URLs/ids, one per line


PositiveInt = Annotated[int, Field(strict=True, gt=0)]


class WorkspaceText(BaseModel):
    text: str


class WorkspaceIds(BaseModel):
    ids: list[PositiveInt]


class WorkspaceTrack(BaseModel):
    track_id: PositiveInt


class DeleteTracksReq(BaseModel):
    track_ids: list[PositiveInt]


class DeleteConfirmReq(DeleteTracksReq):
    confirm: str
    token: str


class CleanupConfirmReq(BaseModel):
    confirm: str
    token: str


def _selection_response(ids):
    try:
        snapshot = db.workspace_selection(ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "items": list(snapshot["items"]),
        "skipped_duplicate_item_ids": list(snapshot["skipped_duplicate_item_ids"]),
    }


def _playlist_batches(snapshot):
    items = list(snapshot["unique_items"])
    batches = []
    for start in range(0, len(items), 50):
        batch = items[start:start + 50]
        ids = [item["youtube_id"] for item in batch]
        url = playlist_generator.build_playlists(ids)[0]
        batches.append({
            "number": len(batches) + 1,
            "item_ids": [item["id"] for item in batch],
            "youtube_ids": ids,
            "playlist_url": url,
            "count": len(batch),
        })
    return batches


def _csv_safe(value):
    if isinstance(value, str) and value and value[0] in "=+-@\t\r":
        return "'" + value
    return value


def _download_response(snapshot, format_name):
    items = list(snapshot["unique_items"])
    if format_name == "ids":
        body = "".join(item["youtube_id"] + "\n" for item in items)
        media_type, filename = "text/plain; charset=utf-8", "workspace-ids.txt"
    elif format_name == "urls":
        body = "".join(item["youtube_url"] + "\n" for item in items)
        media_type, filename = "text/plain; charset=utf-8", "workspace-urls.txt"
    elif format_name in ("playlists", "playlist-links"):
        body = "".join(batch["playlist_url"] + "\n" for batch in _playlist_batches(snapshot))
        media_type, filename = "text/plain; charset=utf-8", "workspace-playlists.txt"
    elif format_name == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(("workspace_item_id", "youtube_id", "youtube_url", "title",
                         "channel", "provenance", "track_id"))
        for item in items:
            writer.writerow(tuple(_csv_safe(value) for value in (
                item["id"], item["youtube_id"], item["youtube_url"], item.get("title"),
                item.get("channel"), item.get("provenance"), item.get("track_id"))))
        body = stream.getvalue()
        media_type, filename = "text/csv; charset=utf-8", "workspace.csv"
    else:
        raise HTTPException(status_code=400, detail="format must be ids, urls, csv, or playlist-links")
    skipped = snapshot["skipped_duplicate_item_ids"]
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Workspace-Skipped-Duplicate-Count": str(len(skipped)),
    }
    return StreamingResponse(iter((body.encode("utf-8"),)), media_type=media_type,
                             headers=headers)


def _parse_workspace_text(text):
    seen = set()
    parsed = []
    for raw in text.splitlines():
        value = raw.strip()
        if not value:
            continue
        youtube_id = value if _YT_ID_RE.fullmatch(value) else None
        if youtube_id is None:
            candidate = value
            if " " in candidate:
                candidate = next((part for part in candidate.split() if "://" in part), candidate)
            try:
                url = urlparse(candidate)
                host = (url.hostname or "").lower()
                if host in ("youtube.com", "www.youtube.com") and url.path == "/watch":
                    youtube_id = parse_qs(url.query).get("v", [None])[0]
                elif host == "youtu.be":
                    youtube_id = url.path.lstrip("/").split("/", 1)[0]
            except ValueError:
                youtube_id = None
        if not youtube_id or not _YT_ID_RE.fullmatch(youtube_id):
            parsed.append({"status": "invalid", "input": value, "reason": "not a YouTube ID or URL"})
        elif youtube_id in seen:
            parsed.append({"status": "duplicate", "input": value, "youtube_id": youtube_id,
                           "youtube_url": "https://www.youtube.com/watch?v=" + youtube_id})
        else:
            seen.add(youtube_id)
            parsed.append({"input": value, "youtube_id": youtube_id,
                           "youtube_url": "https://www.youtube.com/watch?v=" + youtube_id,
                           "provenance": "paste"})
    return parsed


@app.get("/api/counts")
def api_counts():
    return db.counts()


@app.post("/api/playlists")
def api_playlists(req: PlaylistReq):
    """Turn pasted YouTube URLs/ids into watch_videos playlist URLs (the original
    playlist_generator, in the browser). Chunks ids in groups of 50."""
    ids = playlist_generator.extract_ids(req.text.splitlines())
    return {"id_count": len(ids), "playlists": playlist_generator.build_playlists(ids)}


def _record_for_track(track_id):
    links = db.local_deletion_links([track_id])
    if not links:
        return None
    row = links[0]
    return next((r for r in _CATALOG.records
                 if r["folder_identity"] == row["folder_identity"] and
                 r["relative_path"] == row["relative_path"]), None)


def _record_for_ref(folder_identity, relative_path):
    return next((r for r in _CATALOG.records
                 if r["folder_identity"] == folder_identity and r["relative_path"] == relative_path), None)


def _tags_from_record(record):
    """Best-effort artist/title from a file's tags (link-less/file-only items)."""
    path = _safe_delete_record(record) if record is not None else None
    if not path:
        return {}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=True)
        if not audio:
            return {}
        artist = audio.get("artist") or []
        title = audio.get("title") or []
        return {"tag_artist": artist[0] if artist else None,
                "tag_title": title[0] if title else None}
    except Exception:
        return {}


@app.get("/api/workspace")
def api_workspace():
    items = db.list_workspace()
    for item in items:
        if item.get("youtube_id") or item.get("track_title"):
            continue
        record = None
        if item.get("track_id"):
            record = _record_for_track(item["track_id"])
        elif item.get("relative_path"):
            record = _record_for_ref(item["folder_identity"], item["relative_path"])
        if record is not None:
            item.update(_tags_from_record(record))
    return {"items": items}


class WorkspaceEnrich(BaseModel):
    ids: list[PositiveInt] | None = None
    limit: int = 40


# yt-dlp stderr phrases that mean a video is gone vs. locked. Anything else on a
# failed fetch stays "unknown" so a network blip never false-strikes a live link.
_YT_PRIVATE_MARKERS = ("private video", "this video is private")
_YT_DEAD_MARKERS = ("video unavailable", "has been removed", "no longer available",
                    "account associated with this video has been terminated",
                    "removed by the user", "removed for violating")


def _classify_yt_error(text):
    low = (text or "").lower()
    if any(m in low for m in _YT_PRIVATE_MARKERS):
        return "private"
    if any(m in low for m in _YT_DEAD_MARKERS):
        return "dead"
    return "unknown"


def _ytdlp_base():
    """yt-dlp invocation base. Prefer the installed module over a PATH `yt-dlp`
    exe — the exe is often a stale standalone build YouTube now rejects
    ("not available on this app")."""
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    yt = shutil.which("yt-dlp")
    return [yt] if yt else [sys.executable, "-m", "yt_dlp"]


def _resolve_yt_metadata(yt_id):
    """Fetch title/channel/views/health for one video via `yt-dlp -J`. Network.
    Always returns a dict with a 'health' key. Pulled out so tests can stub it."""
    cmd = _ytdlp_base()
    cmd += ["-J", "--no-playlist", "--no-warnings",
            f"https://www.youtube.com/watch?v={yt_id}"]
    cookies = os.path.join(REPO_ROOT, "cookies.txt")
    if os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return {"health": "unknown"}
    if out.returncode != 0:
        return {"health": _classify_yt_error(out.stderr)}
    try:
        data = json.loads(out.stdout)
    except (ValueError, TypeError):
        return {"health": "unknown"}
    return {
        "health": "ok",
        "title": data.get("title"),
        "channel": data.get("channel") or data.get("uploader"),
        "view_count": data.get("view_count"),
        "duration": data.get("duration"),
        "upload_date": data.get("upload_date"),
        "verified": bool(data.get("channel_is_verified")),
        "is_music": "Music" in (data.get("categories") or []),
    }


@app.post("/api/workspace/enrich")
def api_workspace_enrich(req: WorkspaceEnrich):
    """Resolve YouTube metadata + health for Workspace items (yt-dlp). Caches into
    each item's metadata_json so repeat loads skip; caps work per request so a big
    workspace enriches in loops instead of one long blocking call.
    ponytail: sequential fetches, per-item timeout; no global reservation since
    it's read-only network. Front end loops on `remaining` until zero."""
    items = [it for it in db.list_workspace() if it.get("youtube_id")]  # link-less items have nothing to resolve
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in items if it["id"] in wanted]
    else:
        # Unenriched OR previously-unknown (transient fetch fail) — retry those.
        targets = [it for it in items if _item_metadata(it).get("health") in (None, "unknown")]
    remaining = max(0, len(targets) - max(1, req.limit))
    for it in targets[:max(1, req.limit)]:
        db.set_workspace_metadata(it["id"], _resolve_yt_metadata(it["youtube_id"]))
    return {"items": db.list_workspace(),
            "checked": [it["id"] for it in targets[:max(1, req.limit)]],
            "remaining": remaining}


def _item_metadata(item):
    raw = item.get("metadata_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


@app.get("/api/saved-links")
def api_saved_links():
    return {"links": db.list_saved_links()}


class SavedLinkMatch(BaseModel):
    saved_link_id: PositiveInt
    track_id: PositiveInt
    folder_identity: str
    relative_path: str


@app.post("/api/saved-links/match")
def api_saved_link_match(req: SavedLinkMatch):
    record = _record_for_ref(req.folder_identity, req.relative_path)
    if record is None or not os.path.isfile(record["path"]):
        raise HTTPException(status_code=400, detail="local catalog identity is not current")
    try:
        with jobs.curation_write_guard():
            return {"link": db.match_saved_link(
                req.saved_link_id, req.track_id, req.folder_identity, req.relative_path,
                record.get("file_size"), record.get("modified_at"))}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/workspace/selection")
def api_workspace_selection(req: WorkspaceIds):
    return _selection_response(req.ids)


@app.post("/api/workspace/selection/playlists")
@app.post("/api/workspace/playlists")
def api_workspace_playlists(req: WorkspaceIds):
    try:
        snapshot = db.workspace_selection(req.ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"batches": _playlist_batches(snapshot),
            "skipped_duplicate_item_ids": list(snapshot["skipped_duplicate_item_ids"])}


@app.post("/api/workspace/selection/download/{format_name}")
@app.post("/api/workspace/download/{format_name}")
def api_workspace_download(format_name: str, req: WorkspaceIds):
    try:
        snapshot = db.workspace_selection(req.ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return _download_response(snapshot, format_name)


@app.post("/api/workspace/import")
def api_workspace_import(req: WorkspaceText):
    parsed = _parse_workspace_text(req.text)
    inserted = iter(db.import_workspace_items(
        [item for item in parsed if "status" not in item]
    ))
    results = []
    for item in parsed:
        results.append(next(inserted) if "status" not in item else item)
    return {
        "results": results,
        "added": [r for r in results if r["status"] == "added"],
        "duplicates": [r for r in results if r["status"] == "duplicate"],
        "invalid": [r for r in results if r["status"] == "invalid"],
    }


@app.post("/api/workspace/library")
def api_workspace_library(req: WorkspaceTrack):
    try:
        with jobs.curation_write_guard():
            return {"item": db.promote_library_track(req.track_id)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/api/workspace")
def api_workspace_remove(req: WorkspaceIds):
    try:
        return {"items": db.remove_workspace_items(req.ids)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/workspace/reorder")
def api_workspace_reorder(req: WorkspaceIds):
    try:
        return {"items": db.reorder_workspace(req.ids)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/workspace/save-links")
def api_workspace_save_links(req: WorkspaceIds):
    try:
        with jobs.curation_write_guard():
            return db.save_workspace_links(req.ids)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _workspace_run_view(run_id):
    try:
        result = db.get_workspace_run(run_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if result["status"] in ("queued", "running", "finalizing"):
        job = jobs.state("downloader")
        result["job_name"] = "downloader"
        result["job"] = job if job.get("run_id") == run_id else None
    return result


@app.post("/api/workspace/runs/download")
def api_workspace_download_run(req: WorkspaceIds):
    try:
        snapshot = db.workspace_selection(req.ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    try:
        jobs.reserve_pipeline("workspace_download")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    run_id = None
    input_path = None
    launched = False
    try:
        os.makedirs(RUN_STORAGE, exist_ok=True)
        run_id = db.create_workspace_run(
            "download", None, "workspace-selection", snapshot["unique_items"]
        )
        input_path = os.path.join(RUN_STORAGE, f"run-{run_id}-{uuid.uuid4().hex}.ids")
        with open(input_path, "w", encoding="utf-8", newline="") as stream:
            for item in snapshot["unique_items"]:
                stream.write(item["youtube_id"] + "\n")
        conn = db.connect()
        try:
            conn.execute("UPDATE workspace_runs SET input_path = ? WHERE id = ?",
                         (input_path, run_id))
            conn.commit()
        finally:
            conn.close()

        def finalize(_name):
            try:
                outcome = jobs.finalization_result("downloader", run_id)
                status = "stopped" if outcome["stopped"] else (
                    "done" if outcome["returncode"] == 0 else "failed")
                error = None if status == "done" else f"downloader exit code {outcome['returncode']}"
                db.update_workspace_run(run_id, status, error)
            finally:
                try:
                    os.remove(input_path)
                except OSError:
                    pass

        db.update_workspace_run(run_id, "running")
        jobs.start(
            "downloader",
            env_overrides={"YT_INPUT_FILE": input_path},
            finalize=finalize,
            reservation_name="workspace_download",
            run_id=run_id,
        )
        launched = True
        result = _workspace_run_view(run_id)
        result["skipped_duplicate_item_ids"] = list(snapshot["skipped_duplicate_item_ids"])
        return result
    except Exception as e:
        if run_id is not None:
            try:
                db.update_workspace_run(run_id, "failed", str(e))
            except Exception:
                pass
        if input_path and not launched:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if not launched:
            jobs.release_pipeline("workspace_download")
        if isinstance(e, (ValueError, KeyError)):
            raise HTTPException(status_code=400, detail=str(e))
        if isinstance(e, RuntimeError):
            raise HTTPException(status_code=409, detail=str(e))
        raise


@app.get("/api/workspace/runs")
def api_workspace_runs():
    return {"runs": db.list_workspace_runs()}


@app.get("/api/workspace/runs/{run_id}")
def api_workspace_run(run_id: int):
    return _workspace_run_view(run_id)


@app.get("/api/rows")
def api_rows(status: str = "all", limit: int = 200, offset: int = 0):
    rows, total = db.get_rows(status=status, limit=limit, offset=offset)
    for r in rows:
        r["has_local"] = _has_local(r["filename"])
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
               "yt_title", "duration", "check", "yt_health")


def _downloaded_ids():
    """YouTube ids that have a file in the download folder (downloader names files
    `... [<id>].ext`). Distinct from mp3-folder local files."""
    try:
        root = _safe_download_root()
    except HTTPException:
        return set()
    ids = set()
    if not root:
        return ids
    try:
        for name in os.listdir(root):
            yt_id = _download_id(name)
            if yt_id:
                ids.add(yt_id)
    except OSError:
        pass
    return ids


@app.get("/api/library")
def api_library():
    """All tracks, trimmed + tagged with a state, for the browse/list view."""
    rows, total = db.get_rows(status="all", limit=1_000_000, offset=0)
    downloaded = _downloaded_ids()
    out = []
    for r in rows:
        has_local = _has_local(r["filename"])
        item = {k: r.get(k) for k in _LIB_FIELDS}
        item["has_local"] = has_local
        item["downloaded"] = bool(r.get("yt_id") and r["yt_id"] in downloaded)
        item["state"] = _track_state(r, has_local)
        out.append(item)
    return {"rows": out, "total": total}


class LibraryUnreview(BaseModel):
    track_ids: list[PositiveInt]


@app.post("/api/library/unreview")
def api_library_unreview(req: LibraryUnreview):
    """Return tracks to unreviewed (clear the check mark)."""
    try:
        with jobs.curation_write_guard():
            db.reset_track_review(req.track_ids)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


class LibraryVerify(BaseModel):
    ids: list[PositiveInt] | None = None
    limit: int = 30


@app.post("/api/library/verify")
def api_library_verify(req: LibraryVerify):
    """Health-check Library tracks' YouTube links (yt-dlp) and store on the track.
    Caps work per call; front end loops on `remaining` like Workspace enrich."""
    rows, _ = db.get_rows(status="all", limit=1_000_000, offset=0)
    linked = [r for r in rows if r.get("yt_id")]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [r for r in linked if r["id"] in wanted]
    else:
        targets = [r for r in linked if not r.get("yt_health")]
    cap = max(1, req.limit)
    for r in targets[:cap]:
        db.set_track_health(r["id"], _resolve_yt_metadata(r["yt_id"]).get("health", "unknown"))
    return {"rows": api_library()["rows"], "checked": [r["id"] for r in targets[:cap]],
            "remaining": max(0, len(targets) - cap)}


# ── background verify tasks + Activity log ──────────────────────────────────
class VerifyScope(BaseModel):
    scope: str = "unverified"          # "all" | "unverified"


def _resolve_health(yt_id):
    """Resolve one link's health; NetworkDown on 'unknown' so the worker's
    consecutive-failure cutoff can trip when the network/yt-dlp is down."""
    health = _resolve_yt_metadata(yt_id).get("health", "unknown")
    if health == "unknown":
        raise tasks.NetworkDown()
    return health


@app.post("/api/tasks/verify/library")
def api_task_verify_library(req: VerifyScope):
    rows, _ = db.get_rows(status="all", limit=1_000_000, offset=0)
    linked = [r for r in rows if r.get("yt_id")]
    targets = linked if req.scope == "all" else [r for r in linked if not r.get("yt_health")]
    yt = {r["id"]: r["yt_id"] for r in targets}

    def do_one(track_id):
        try:
            health = _resolve_health(yt[track_id])
        except tasks.NetworkDown:
            db.set_track_health(track_id, "unknown")
            raise
        db.set_track_health(track_id, health)
        return health in ("dead", "private")

    try:
        return tasks.run("library-verify", "Verify library links", list(yt), do_one)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/tasks/verify/workspace")
def api_task_verify_workspace(req: VerifyScope):
    items = [it for it in db.list_workspace() if it.get("youtube_id")]
    targets = items if req.scope == "all" else [
        it for it in items if _item_metadata(it).get("health") in (None, "unknown")]
    yt = {it["id"]: it["youtube_id"] for it in targets}

    def do_one(item_id):
        meta = _resolve_yt_metadata(yt[item_id])
        db.set_workspace_metadata(item_id, meta)
        if meta.get("health", "unknown") == "unknown":
            raise tasks.NetworkDown()
        return meta.get("health") in ("dead", "private")

    try:
        return tasks.run("workspace-verify", "Verify workspace links", list(yt), do_one)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/tasks")
def api_tasks():
    return {"tasks": tasks.snapshot()}


@app.post("/api/tasks/{task_id}/cancel")
def api_task_cancel(task_id: int):
    return {"ok": tasks.request_cancel(task_id)}


@app.get("/api/history")
def api_history(limit: int = 200):
    """The append-only approve/reject decision log (Activity › History)."""
    return {"decisions": db.list_decisions(limit)}


def _delete_downloads_for_ids(ids):
    """Remove files in the download folder whose name carries one of these ids.
    Contained + reparse-checked; never touches mp3-folder files."""
    if not ids:
        return
    try:
        root = _safe_download_root()
    except HTTPException:
        return
    if not root:
        return
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        yt_id = _download_id(name)
        if yt_id is None or yt_id not in ids:
            continue
        path = os.path.normpath(os.path.join(root, name))
        try:
            if os.path.commonpath((root, path)) != root or _is_reparse_point(path) or not os.path.isfile(path):
                continue
            os.remove(path)
        except (OSError, ValueError):
            pass


class LibraryRemove(BaseModel):
    track_ids: list[PositiveInt]


@app.post("/api/library/remove")
def api_library_remove(req: LibraryRemove):
    """Delete Library entries (track rows + links; also their downloaded files).
    Does not delete mp3-folder files — that stays the approved-deletion flow."""
    try:
        with jobs.curation_write_guard():
            removed_ids = db.remove_tracks(req.track_ids)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    _delete_downloads_for_ids({i for i in removed_ids if i})
    return {"removed": len(removed_ids)}


def _local_files():
    return db.local_file_classifications(_CATALOG.records)


def _safe_delete_record(record):
    try:
        for lexical_root in settings.configured_mp3_folders():
            root = os.path.realpath(lexical_root)
            if os.path.normcase(root) != os.path.normcase(record["folder_identity"]):
                continue
            lexical = os.path.normpath(os.path.join(lexical_root, record["relative_path"]))
            if os.path.commonpath((os.path.normcase(lexical_root),
                                   os.path.normcase(lexical))) != os.path.normcase(lexical_root):
                continue
            current = os.path.normpath(lexical_root)
            for part in os.path.relpath(lexical, lexical_root).split(os.sep):
                current = os.path.join(current, part)
                if _is_reparse_point(current):
                    break
            else:
                resolved = os.path.realpath(lexical)
                if os.path.commonpath((root, resolved)) != root or not os.path.isfile(lexical):
                    continue
                return lexical
        return False
    except (OSError, ValueError):
        return False


def _delete_targets(track_ids):
    if len(track_ids) != len(set(track_ids)):
        raise HTTPException(status_code=400, detail="track_ids must be unique")
    rows = db.local_deletion_links(track_ids)
    by_track = {}
    for row in rows:
        by_track.setdefault(row["track_id"], []).append(row)
    targets = []
    for track_id in track_ids:
        links = by_track.get(track_id, [])
        if len(links) != 1:
            raise HTTPException(status_code=409, detail=f"track {track_id} has no unique available local file")
        row = links[0]
        if row["check"] != 1:
            raise HTTPException(status_code=409, detail=f"track {track_id} is not approved")
        record = _record_for_ref(row["folder_identity"], row["relative_path"])
        path = _safe_delete_record(record) if record is not None else False
        if not path:
            raise HTTPException(status_code=409, detail=f"track {track_id} local file is missing or unsafe")
        info = os.stat(path)
        targets.append({"track_id": track_id, "folder_identity": record["folder_identity"],
                        "relative_path": record["relative_path"], "file_size": info.st_size,
                        "modified_at": str(info.st_mtime_ns), "identity": (info.st_dev, info.st_ino),
                        "record": record, "delete_path": path})
    return targets


def _target_state(targets):
    return [{k: target[k] for k in ("track_id", "folder_identity", "relative_path", "file_size", "modified_at", "identity")}
            for target in targets]


def _new_token(kind, payload):
    token = secrets.token_urlsafe(24)
    _DELETE_TOKENS[token] = {"kind": kind, "expires": time.time() + _DELETE_TOKEN_TTL, **payload}
    return token


def _take_token(token, kind):
    data = _DELETE_TOKENS.pop(token, None)
    if not data or data["kind"] != kind or data["expires"] < time.time():
        raise HTTPException(status_code=409, detail="preview token expired or invalid")
    return data


@app.post("/api/library/delete/preview")
def api_library_delete_preview(req: DeleteTracksReq):
    targets = _delete_targets(req.track_ids)
    state = _target_state(targets)
    token = _new_token("library-delete", {"track_ids": list(req.track_ids), "state": state})
    return {"token": token, "expires_in": _DELETE_TOKEN_TTL,
            "targets": [{k: item[k] for k in ("track_id", "folder_identity", "relative_path", "file_size", "modified_at")}
                        for item in state]}


@app.post("/api/library/delete")
def api_library_delete(req: DeleteConfirmReq):
    if req.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="type DELETE to confirm")
    data = _take_token(req.token, "library-delete")
    if list(req.track_ids) != data["track_ids"]:
        raise HTTPException(status_code=409, detail="selection does not match preview")
    try:
        jobs.reserve_pipeline("library_delete")
        with jobs.curation_write_guard():
            return _perform_library_delete(req, data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        jobs.release_pipeline("library_delete")


def _perform_library_delete(req, data):
    try:
        targets = _delete_targets(req.track_ids)
    except HTTPException as exc:
        db.finish_local_deletions([{**item, "outcome": "rejected", "detail": exc.detail}
                                    for item in data["state"]])
        raise
    if _target_state(targets) != data["state"]:
        db.finish_local_deletions([{**item, "outcome": "rejected",
                                    "detail": "local targets changed since preview"}
                                   for item in data["state"]])
        raise HTTPException(status_code=409, detail="local targets changed since preview")
    deleted = []
    try:
        for target in targets:
            current = _safe_delete_record(target["record"])
            info = os.stat(current) if current else None
            if (not current or _is_reparse_point(current) or info.st_size != target["file_size"] or
                    str(info.st_mtime_ns) != target["modified_at"] or
                    (info.st_dev, info.st_ino) != target["identity"]):
                raise OSError("local file changed during confirmation")
            os.remove(current)
            deleted.append(target)
    except OSError as exc:
        db.finish_local_deletions(
            [{**target, "outcome": "deleted"} for target in deleted] +
            [{**target, "outcome": "rejected", "detail": str(exc)}
             for target in targets if target not in deleted])
        try:
            _install_catalog(_build_file_catalog(settings.configured_mp3_folders()))
        finally:
            raise HTTPException(status_code=409, detail="delete changed during confirmation")
    db.finish_local_deletions([{**target, "outcome": "deleted"} for target in targets])
    _install_catalog(_build_file_catalog(settings.configured_mp3_folders()))
    return {"deleted": [{"track_id": target["track_id"], "folder_identity": target["folder_identity"],
                          "relative_path": target["relative_path"]} for target in targets]}


@app.get("/api/library/delete/audit")
def api_library_delete_audit():
    return {"audit": db.list_deletion_audit()}


@app.get("/api/local-files")
def api_local_files():
    files = _local_files()
    return {"files": files, "rows": files, "total": len(files)}


class LocalFileRef(BaseModel):
    folder_identity: str
    relative_path: str


class LocalFilesAdd(BaseModel):
    files: list[LocalFileRef]


@app.post("/api/library/add-files")
def api_library_add_files(req: LocalFilesAdd):
    """Turn untracked local files into Library entries (find-or-create a track,
    then link the file). Bulk; returns a per-file result."""
    results = []
    try:
        with jobs.curation_write_guard():
            for ref in req.files:
                record = _record_for_ref(ref.folder_identity, ref.relative_path)
                if record is None or not os.path.isfile(record["path"]):
                    results.append({"relative_path": ref.relative_path, "added": False,
                                    "reason": "file not in catalog"})
                    continue
                try:
                    out = db.add_local_file_to_library(
                        ref.folder_identity, ref.relative_path, record["basename"],
                        record.get("file_size"), record.get("modified_at"))
                    results.append({"relative_path": ref.relative_path, "added": True, **out})
                except ValueError as e:
                    results.append({"relative_path": ref.relative_path, "added": False, "reason": str(e)})
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"results": results, "added": sum(r["added"] for r in results)}


@app.post("/api/workspace/add-files")
def api_workspace_add_files(req: LocalFilesAdd):
    """Stage local files directly in the Workspace as file-only items — no Library
    entry is created (the item references the file, not a track)."""
    results = []
    try:
        with jobs.curation_write_guard():
            for ref in req.files:
                record = _record_for_ref(ref.folder_identity, ref.relative_path)
                if record is None or not os.path.isfile(record["path"]):
                    results.append({"relative_path": ref.relative_path, "added": False, "reason": "file not in catalog"})
                    continue
                item = db.add_workspace_file(ref.folder_identity, ref.relative_path)   # title null -> tags/basename win
                results.append({"relative_path": ref.relative_path, "added": True, "workspace_item_id": item["id"]})
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"results": results, "added": sum(r["added"] for r in results)}


def _download_file_path(yt_id):
    """Absolute safe path of the download-folder file for this id, or None."""
    try:
        root = _safe_download_root()
    except HTTPException:
        return None
    if not root:
        return None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        if _download_id(name) == yt_id:
            path = os.path.normpath(os.path.join(root, name))
            try:
                if os.path.commonpath((root, path)) == root and not _is_reparse_point(path) and os.path.isfile(path):
                    return path
            except ValueError:
                return None
    return None


class RevealRef(BaseModel):
    track_id: int | None = None
    folder_identity: str | None = None
    relative_path: str | None = None
    download_yt_id: str | None = None


@app.post("/api/reveal")
def api_reveal(req: RevealRef):
    """Open the OS file browser with the file selected (localhost only, so the
    server host is the user's machine). Path resolved + safety-checked first."""
    path = None
    if req.download_yt_id is not None:
        path = _download_file_path(req.download_yt_id)
    else:
        record = None
        if req.track_id is not None:
            links = db.local_deletion_links([req.track_id])
            link = links[0] if links else None
            if link:
                record = _record_for_ref(link["folder_identity"], link["relative_path"])
        elif req.folder_identity and req.relative_path:
            record = _record_for_ref(req.folder_identity, req.relative_path)
        path = _safe_delete_record(record) if record is not None else None
    if not path:
        raise HTTPException(status_code=404, detail="file missing or unsafe")
    # explorer.exe parses its own command line; the list form mis-parses and it
    # falls back to Documents. Pass one quoted string (Windows CreateProcess).
    try:
        subprocess.run('explorer /select,"%s"' % os.path.normpath(path), timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"ok": True}


@app.get("/api/download-audio")
def api_download_audio(yt_id: str):
    """Serve the download-folder file for a YouTube id (preview)."""
    path = _download_file_path(yt_id)
    if not path:
        raise HTTPException(status_code=404, detail="downloaded file not found")
    media_type = _AUDIO_MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


class DownloadDelete(BaseModel):
    yt_ids: list[str]


@app.post("/api/download/delete")
def api_download_delete(req: DownloadDelete):
    """Delete download-folder files for these ids (app's own output, not mp3-folder files)."""
    _delete_downloads_for_ids({i for i in req.yt_ids if i})
    return {"ok": True}


@app.get("/api/local-audio")
def api_local_audio(folder_identity: str, relative_path: str):
    """Serve an untracked catalog file read-only (preview). Containment + reparse
    validated via the same safe-path resolver used for deletion."""
    record = _record_for_ref(folder_identity, relative_path)
    if record is None:
        raise HTTPException(status_code=404, detail="file not in catalog")
    path = _safe_delete_record(record)
    if not path:
        raise HTTPException(status_code=404, detail="local file missing or unsafe")
    media_type = _AUDIO_MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


class LocalFileMatch(BaseModel):
    folder_identity: str
    relative_path: str


@app.post("/api/local-files/match")
def api_local_file_match(req: LocalFileMatch):
    """Link an untracked local file to an existing track by filename."""
    record = _record_for_ref(req.folder_identity, req.relative_path)
    if record is None or not os.path.isfile(record["path"]):
        raise HTTPException(status_code=400, detail="local catalog identity is not current")
    try:
        with jobs.curation_write_guard():
            return db.match_local_file_by_name(
                req.folder_identity, req.relative_path, record["basename"],
                record.get("file_size"), record.get("modified_at"))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/untracked")
def api_untracked():
    files = [f for f in _local_files() if f["category"] != "verified"]
    return {"files": files, "rows": files, "total": len(files),
            "categories": {category: sum(f["category"] == category for f in files)
                           for category in ("verified", "rejected", "unreviewed", "unmatched", "ambiguous")}}


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
    r["has_local"] = _has_local(r["filename"])
    return r


@app.post("/api/decision")
def api_decision(d: Decision):
    global _decision_count
    try:
        with jobs.curation_write_guard():
            result = db.record_decision(d.track_id, d.decision)
            _decision_count += 1
            if AUTO_EXPORT_EVERY and _decision_count % AUTO_EXPORT_EVERY == 0:
                db.export_csv_only()       # atomic; marks flow to the git-tracked CSV
                result["auto_exported"] = True
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return result


@app.post("/api/export")
def api_export():
    try:
        with jobs.curation_write_guard():
            return db.export_matches()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# --- Settings (.env secrets) ------------------------------------------------
class SettingsIn(BaseModel):
    DISCORD_BOT_TOKEN: str | None = None
    DISCORD_CHANNEL_ID: str | None = None
    ACOUSTID_API_KEY: str | None = None
    MP3_FOLDERS_JSON: list[str] | str | None = None


def _native_pick_folder():
    """Open the OS-native folder dialog and return the chosen absolute path (or None).
    Runs tkinter in a subprocess so Tk owns a real main thread and never touches the
    server event loop. ponytail: works because this is a localhost app -- the dialog
    opens on the same machine as the browser; not valid if ever served remotely."""
    code = (
        "import tkinter, tkinter.filedialog as fd\n"
        "r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "print(fd.askdirectory() or '')\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    path = out.stdout.strip()
    return os.path.normpath(path) if path else None


@app.post("/api/pick-folder")
def api_pick_folder():
    """Pop the native folder picker on the server machine (localhost). Returns the
    picked path; the client adds it to the editable folder list, then saves+rescans."""
    path = _native_pick_folder()
    if not path:
        raise HTTPException(status_code=409, detail="folder picker cancelled or unavailable")
    return {"path": path}


@app.get("/api/settings")
def api_settings_get():
    return settings.public_view()


@app.post("/api/settings")
def api_settings_set(s: SettingsIn):
    # only persist keys the client actually sent (others stay as-is)
    sent = {k: v for k, v in s.model_dump().items() if v is not None}
    if "MP3_FOLDERS_JSON" not in sent:
        return settings.save(sent)
    try:
        jobs.reserve_pipeline("settings_folders")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    try:
        folders = settings.parse_mp3_folders(sent["MP3_FOLDERS_JSON"])
        catalog = _build_file_catalog(folders)
        result = settings.save(sent)
        _install_catalog(catalog)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        jobs.release_pipeline("settings_folders")


_CLEANUP_EXTENSIONS = (".mp4", ".webm", ".m4a", ".mp4.part", ".webm.part", ".m4a.part",
                       ".mp4.ytdl", ".webm.ytdl", ".m4a.ytdl")


def _cleanup_download_targets():
    folder = _safe_download_root()
    if folder is None:
        return []
    targets = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or _is_reparse_point(path):
            continue
        if not (name.endswith(_CLEANUP_EXTENSIONS) or os.path.getsize(path) == 0):
            continue
        info = os.stat(path)
        targets.append({"relative_path": name, "file_size": info.st_size,
                        "modified_at": str(info.st_mtime_ns),
                        "identity": (info.st_dev, info.st_ino)})
    return targets


def _default_download_root():
    return os.path.normpath(os.path.join(REPO_ROOT, "downloads"))


def _safe_download_root():
    """Validate the configured download folder before cleanup resolves it. The
    folder is user-chosen (Settings) but every ancestor is still checked for
    symlinks/reparse points, and per-target containment is enforced by the
    caller, so cleanup can only touch files inside this exact folder."""
    try:
        lexical_root = settings.configured_download_folder(_default_download_root())
    except ValueError:
        return None
    lexical_root = os.path.normpath(lexical_root)
    if not os.path.lexists(lexical_root):
        return None
    current = lexical_root
    try:
        while True:
            if _is_reparse_point(current):
                raise HTTPException(status_code=409, detail="downloads root contains symlink or reparse point")
            parent = os.path.dirname(current)
            if parent == current:   # reached the drive/filesystem root
                break
            current = parent
        if not os.path.isdir(lexical_root):
            return None
        resolved = os.path.realpath(lexical_root)
        return resolved if os.path.isdir(resolved) else None
    except ValueError:
        raise HTTPException(status_code=409, detail="downloads root is on another drive")


def _rewrite_download_checkpoint(ids):
    if not ids:
        return
    path = os.path.join(REPO_ROOT, "downloaded_ids.txt")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", newline="") as stream:
        lines = stream.readlines()
    tmp = path + ".tmp"
    kept = [line for line in lines if line.strip() not in ids]
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as stream:
            stream.writelines(kept)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


@app.post("/api/settings/cleanup-downloads/preview")
def api_cleanup_downloads_preview():
    targets = _cleanup_download_targets()
    token = _new_token("cleanup-downloads", {"state": targets})
    return {"token": token, "expires_in": _DELETE_TOKEN_TTL,
            "targets": [{k: item[k] for k in ("relative_path", "file_size", "modified_at")}
                        for item in targets]}


@app.post("/api/settings/cleanup-downloads")
def api_cleanup_downloads(req: CleanupConfirmReq):
    if req.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="type DELETE to confirm")
    data = _take_token(req.token, "cleanup-downloads")
    try:
        jobs.reserve_pipeline("cleanup_downloads")
        folder = _safe_download_root()
        if folder is None:
            audit = [{"track_id": None,
                      "folder_identity": os.path.normpath(os.path.join(REPO_ROOT, "downloads")),
                      "relative_path": item["relative_path"], "outcome": "rejected",
                      "detail": "downloads root disappeared or is not a directory"}
                     for item in data["state"]]
            db.finish_local_deletions(audit)
            raise HTTPException(status_code=409, detail="downloads root disappeared or is not a directory")
        deleted, rejected = [], []
        for item in data["state"]:
            folder = _safe_download_root()
            if folder is None:
                rejected.extend({**candidate, "detail": "downloads root disappeared or is not a directory"}
                                for candidate in data["state"] if candidate not in deleted)
                break
            path = os.path.normpath(os.path.join(folder, item["relative_path"]))
            try:
                if os.path.commonpath((folder, path)) != folder:
                    raise OSError("target outside downloads")
                info = os.stat(path)
                if (_is_reparse_point(path) or info.st_size != item["file_size"] or
                        str(info.st_mtime_ns) != item["modified_at"] or
                        (info.st_dev, info.st_ino) != tuple(item["identity"])):
                    raise OSError("cleanup target changed")
                os.remove(path)
                deleted.append(item)
            except OSError as exc:
                rejected.append({**item, "detail": str(exc)})
        ids = {yt_id for item in deleted
               if (yt_id := _download_id(item["relative_path"]))}
        checkpoint_error = None
        try:
            _rewrite_download_checkpoint(ids)
        except Exception as exc:
            checkpoint_error = str(exc)
        audit = ([{"track_id": None, "folder_identity": folder,
                   "relative_path": item["relative_path"], "outcome": "deleted",
                   "detail": checkpoint_error}
                  for item in deleted] +
                 [{"track_id": None, "folder_identity": folder,
                   "relative_path": item["relative_path"], "outcome": "rejected",
                   "detail": item["detail"]} for item in rejected])
        db.finish_local_deletions(audit)
        return {"status": "done" if not rejected and not checkpoint_error else "partial",
                "deleted": len(deleted), "rejected": len(rejected),
                "checkpoint_error": checkpoint_error, "audit": audit}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        jobs.release_pipeline("cleanup_downloads")


# --- Discord harvest --------------------------------------------------------
class DiscordFetchIn(BaseModel):
    channel_id: str | None = None
    author: str | None = None
    write_files: bool = True


@app.post("/api/discord/fetch")
def api_discord_fetch(d: DiscordFetchIn):
    reserved = False
    if d.write_files:
        try:
            jobs.reserve_pipeline("discord_fetch")
            reserved = True
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e))
    try:
        channel_id = d.channel_id or settings.get("DISCORD_CHANNEL_ID")
        return discord_service.fetch_and_extract(
            channel_id, author=d.author, write_files=d.write_files
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if reserved:
            jobs.release_pipeline("discord_fetch")


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


def _prepare_pipeline(name):
    if name in jobs.CURATION_READERS or name in jobs.CURATION_WRITERS:
        db.export_matches()


def _finalize_pipeline(name):
    if name not in jobs.CURATION_WRITERS:
        return
    try:
        db.sync_matches_csv()
    except Exception:
        # Restore canonical files from SQLite before reporting finalization
        # failure. The caller still marks the job failed.
        db.export_matches()
        raise
    db.export_matches()


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
    if name == "cleanup_downloads":
        raise HTTPException(status_code=409, detail="use cleanup-downloads preview and confirmation endpoint")
    try:
        curation = name in jobs.CURATION_READERS or name in jobs.CURATION_WRITERS
        return jobs.start(
            name,
            args=(body.args if body else None),
            prepare=_prepare_pipeline if curation else None,
            finalize=_finalize_pipeline if curation else None,
            curation=curation,
        )
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
    record = _record_for_filename(row["filename"])
    if record is None:
        raise HTTPException(status_code=404, detail="local file not found")
    media_type = _AUDIO_MEDIA_TYPES[os.path.splitext(record["path"])[1].lower()]
    return FileResponse(record["path"], media_type=media_type)


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _resolve_yt_audio(yt_id):
    """Direct audio-stream URL for a YouTube id via `yt-dlp -g`, or None. Network.
    Pulled out as its own function so tests can stub it without hitting yt-dlp."""
    cmd = _ytdlp_base()
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
