"""
FastAPI backend for the match-review app.

Safety posture:
  - Audio endpoint is strictly READ-ONLY: it serves files from MP3_FOLDERS and
    nothing else. No endpoint deletes or writes any audio file.
  - Curation writes go through db.py (append-only decisions + atomic export).
"""
import os
import contextlib
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
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost", "https://localhost",
    ],
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
_DELETE_TOKENS = {}   # token TTL is Settings-tunable (settings.delete_token_ttl)

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


def _refresh_catalog():
    """Rebuild the in-memory catalog from disk so a local search/find sees files added
    since startup (e.g. a just-downloaded track). Only the searchable records are swapped
    in — no db.sync_catalog_links — so it's cheap enough to run on an explicit find.
    ponytail: full walk of every configured folder; fine for an on-demand action, add
    incremental/watch indexing if huge libraries make this slow."""
    try:
        _swap_file_catalog(_build_file_catalog(settings.configured_mp3_folders()))
    except ValueError:
        pass   # a folder went missing — keep the last good catalog


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
    decision: bool                    # True = approve, False = reject
    checklist: list[str] | None = None  # verified parts: youtube/local/lyrics/metadata


class PlaylistReq(BaseModel):
    text: str = ""        # pasted URLs/ids, one per line


PositiveInt = Annotated[int, Field(strict=True, gt=0)]


class WorkspaceText(BaseModel):
    text: str


class WorkspaceIds(BaseModel):
    ids: list[PositiveInt]


class WorkspaceDownloadReq(WorkspaceIds):
    format: str = "opus"


class DownloadRunReq(BaseModel):
    yt_ids: list[str]
    format: str = "opus"
    replace: bool = True


class WorkspaceTrack(BaseModel):
    track_id: PositiveInt


class DeleteTracksReq(BaseModel):
    track_ids: list[PositiveInt]


class DeleteConfirmReq(DeleteTracksReq):
    confirm: str
    token: str


class WorkspaceLocalDelete(BaseModel):
    ids: list[PositiveInt]


class WorkspaceLocalDeleteConfirm(WorkspaceLocalDelete):
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


def _read_audio_tags(path):
    """Best-effort artist/title from a file's tags. Reads any existing path (mutagen),
    so out-of-folder staged files work too — not only indexed catalog files."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=True)
        if not audio:
            return {}
        artist = audio.get("artist") or []
        title = audio.get("title") or []
        album = audio.get("album") or []
        return {"tag_artist": artist[0] if artist else None,
                "tag_title": title[0] if title else None,
                "tag_album": album[0] if album else None}
    except Exception:
        return {}


def _tags_from_record(record):
    """Best-effort artist/title from a catalog file's tags (link-less/file-only items)."""
    return _read_audio_tags(_safe_delete_record(record) if record is not None else None)


def _decorate_workspace_items(items):
    """Add the derived fields the UI's labels need onto raw db.list_workspace() rows:
    file-ref classification (is_download_file / downloaded) and best-effort file tags for
    link-less items. EVERY endpoint returning workspace items must route through this, or
    a reload (e.g. the enrich loop) drops the fields and the labels flicker/misfire."""
    downloaded_ids = _downloaded_ids()
    for item in items:
        # A download-folder file is "downloaded", not a local/untracked file. Link-only
        # items are "downloaded" when an id-named file exists in the download folder.
        is_dl_file = _is_download_ref(item)
        item["is_download_file"] = is_dl_file
        item["downloaded"] = is_dl_file or bool(item.get("youtube_id") and item["youtube_id"] in downloaded_ids)
        if item.get("youtube_id") or item.get("track_title"):
            continue
        record = None
        if item.get("track_id"):
            record = _record_for_track(item["track_id"])
        elif item.get("relative_path"):
            record = _record_for_ref(item["folder_identity"], item["relative_path"])
        if record is not None:
            item.update(_tags_from_record(record))
    return items


@app.get("/api/workspace")
def api_workspace():
    return {"items": _decorate_workspace_items(db.list_workspace())}


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
    return {"items": _decorate_workspace_items(db.list_workspace()),
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


def _item_carries_file(item):
    """A Workspace item that carries an untracked local file (so "Save to library" should
    make it a Library track, not a saved link). One predicate, server-side, so bulk and
    per-row routing can never drift."""
    return bool(item.get("relative_path")) and not item.get("track_id")


@app.post("/api/workspace/save-to-library")
def api_workspace_save_to_library(req: WorkspaceIds):
    """"Save to library" for a Workspace selection, routing each item by kind — the single
    place this decision lives, so the per-row action and the bulk button behave identically:
      • carries an untracked file  -> make it a Library track (link carried onto the track)
      • otherwise, has a YouTube id -> a saved link
    Returns per-item outcomes plus the saved-links result for the link subset."""
    items = {it["id"]: it for it in db.list_workspace()}
    saved_tracks, link_ids, results = [], [], []
    try:
        with jobs.curation_write_guard():
            for iid in req.ids:
                item = items.get(iid)
                if item is None:
                    results.append({"id": iid, "outcome": "missing"})
                    continue
                if _item_carries_file(item):
                    fi, rp = item["folder_identity"], item["relative_path"]
                    path = os.path.join(fi, rp)
                    if not os.path.isfile(path):
                        results.append({"id": iid, "outcome": "file-missing"})
                        continue
                    st = os.stat(path)
                    r = db.save_workspace_file_to_library(
                        iid, fi, rp, os.path.basename(rp), st.st_size, str(st.st_mtime_ns),
                        yt_id=item.get("youtube_id"), yt_meta=_item_metadata(item))
                    saved_tracks.append({"id": iid, **r})
                    results.append({"id": iid, "outcome": "track", "track_id": r["track_id"]})
                elif item.get("youtube_id"):
                    link_ids.append(iid)
                    results.append({"id": iid, "outcome": "link"})
                else:
                    results.append({"id": iid, "outcome": "skipped"})
            links = db.save_workspace_links(link_ids) if link_ids else {"added": [], "duplicates": []}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"results": results, "saved_tracks": saved_tracks,
            "saved_link_count": len(links.get("added", [])), "duplicate_link_count": len(links.get("duplicates", []))}


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


def _audio_format(fmt):
    fmt = (fmt or "opus").lower()
    if fmt not in ("opus", "mp3", "m4a"):
        raise HTTPException(status_code=400, detail="format must be opus, mp3, or m4a")
    return fmt


def _start_download_run(items, skipped_ids, fmt, replace):
    """Shared audio-download run: write an ids file, launch the downloader subprocess with
    the chosen codec, track a workspace_run. replace=True re-downloads even already-downloaded
    ids and swaps the old file only on success (downloader writes to .part first, then the app
    removes the stale old-format file post-success — see _remove_stale_after_replace)."""
    try:
        jobs.reserve_pipeline("workspace_download")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    run_id = None
    input_path = None
    launched = False
    # Snapshot the id's existing download files up front (do NOT delete now — only after success).
    pre_files = {it["youtube_id"]: set(_download_files_for_id(it["youtube_id"])) for it in items} if replace else {}
    try:
        os.makedirs(RUN_STORAGE, exist_ok=True)
        run_id = db.create_workspace_run(
            "download", None, "workspace-selection", items
        )
        input_path = os.path.join(RUN_STORAGE, f"run-{run_id}-{uuid.uuid4().hex}.ids")
        with open(input_path, "w", encoding="utf-8", newline="") as stream:
            for item in items:
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
                if replace and status == "done":
                    _remove_stale_after_replace(pre_files)
            finally:
                try:
                    os.remove(input_path)
                except OSError:
                    pass

        db.update_workspace_run(run_id, "running")
        env = {"YT_INPUT_FILE": input_path, "AUDIO_FORMAT": fmt}
        if replace:
            env["YT_FORCE_REDOWNLOAD"] = "1"
        jobs.start(
            "downloader",
            env_overrides=env,
            finalize=finalize,
            reservation_name="workspace_download",
            run_id=run_id,
        )
        launched = True
        result = _workspace_run_view(run_id)
        result["skipped_duplicate_item_ids"] = list(skipped_ids)
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


@app.post("/api/workspace/runs/download")
def api_workspace_download_run(req: WorkspaceDownloadReq):
    try:
        snapshot = db.workspace_selection(req.ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Bulk keeps skip-existing behavior (replace=False); it just picks the codec.
    return _start_download_run(snapshot["unique_items"],
                               snapshot["skipped_duplicate_item_ids"],
                               _audio_format(req.format), replace=False)


@app.post("/api/download/run")
def api_download_run(req: DownloadRunReq):
    """Download one/more YouTube ids straight to the download folder (the YouTube-label
    button on any screen). replace=True → re-download and swap the file on success."""
    seen, items = set(), []
    for raw in req.yt_ids:
        yt_id = (raw or "").strip()
        if yt_id and yt_id not in seen:
            seen.add(yt_id)
            items.append({"youtube_id": yt_id,
                          "youtube_url": f"https://www.youtube.com/watch?v={yt_id}"})
    if not items:
        raise HTTPException(status_code=400, detail="yt_ids required")
    return _start_download_run(items, [], _audio_format(req.format), replace=req.replace)


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


def _download_root_nc():
    """Normcased download-folder path for containment checks, or None."""
    try:
        root = _safe_download_root()
    except HTTPException:
        return None
    return os.path.normcase(os.path.normpath(root)) if root else None


def _is_download_ref(item):
    """True when a Workspace item's own file ref lives in the download folder — i.e. it
    is a downloaded file, not an mp3-folder/untracked local file. (A download and a local
    file are distinct; see CLAUDE.md.)"""
    if not item.get("relative_path"):
        return False
    root_nc = _download_root_nc()
    return bool(root_nc and os.path.normcase(os.path.normpath(item.get("folder_identity") or "")) == root_nc)


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
    scope: str = "unverified"          # "all" | "unverified" (used when ids is None)
    ids: list[int] | None = None       # verify exactly these (the "Verify labels" selection)


def _resolve_health(yt_id):
    """Resolve one link's health; NetworkDown on 'unknown' so the worker's
    consecutive-failure cutoff can trip when the network/yt-dlp is down."""
    health = _resolve_yt_metadata(yt_id).get("health", "unknown")
    if health == "unknown":
        raise tasks.NetworkDown()
    return health


@app.post("/api/tasks/verify/library")
def api_task_verify_library(req: VerifyScope):
    # Refresh the catalog up front so the sweep also re-derives local-file freshness, not just
    # link health — this is the "Verify labels" action. Dead links auto-unreview (set_track_health).
    _refresh_catalog()
    if req.ids is not None:               # selection: verify each one's local file too (same core)
        for track_id in req.ids:
            try:
                _verify_entity_local("track", track_id)
            except HTTPException:
                pass
    rows, _ = db.get_rows(status="all", limit=1_000_000, offset=0)
    linked = [r for r in rows if r.get("yt_id")]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [r for r in linked if r["id"] in wanted]
    else:
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
        return tasks.run("library-verify", "Verify labels", list(yt), do_one)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/tasks/verify/workspace")
def api_task_verify_workspace(req: VerifyScope):
    _refresh_catalog()
    if req.ids is not None:               # selection: verify each one's local file too (same core)
        for item_id in req.ids:
            try:
                _verify_entity_local("workspace", item_id)
            except HTTPException:
                pass
    items = [it for it in db.list_workspace() if it.get("youtube_id")]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in items if it["id"] in wanted]
    else:
        targets = items if req.scope == "all" else [
            it for it in items if _item_metadata(it).get("health") in (None, "unknown")]
    yt = {it["id"]: it["youtube_id"] for it in targets}
    track_of = {it["id"]: it.get("track_id") for it in targets}

    def do_one(item_id):
        meta = _resolve_yt_metadata(yt[item_id])
        db.set_workspace_metadata(item_id, meta)
        health = meta.get("health", "unknown")
        db.unreview_track_if_dead(track_of[item_id], health)   # dead link + approved track -> unreviewed
        if health == "unknown":
            raise tasks.NetworkDown()
        return health in ("dead", "private")

    try:
        return tasks.run("workspace-verify", "Verify labels", list(yt), do_one)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/tasks")
def api_tasks():
    return {"tasks": tasks.snapshot()}


@app.post("/api/tasks/{task_id}/cancel")
def api_task_cancel(task_id: int):
    return {"ok": tasks.request_cancel(task_id)}


# ── auto link finding (file ↔ YouTube) ──────────────────────────────────────
# Reuse the root searcher's ranking (the same score that populated tracks.score at
# review time) so an auto-found link is judged exactly like a reviewed one. Both floors
# are Settings-tunable (settings.yt_min_score / local_min_score).


def _yt_search(query, n):
    """Top-n YouTube search hits (full metadata) via `yt-dlp -J ytsearchN:`. Network.
    Returns a list of entry dicts; [] on any failure. Pulled out so tests can stub it."""
    if not (query or "").strip():
        return []
    cmd = _ytdlp_base() + ["-J", "--no-warnings", f"ytsearch{max(1, n)}:{query}"]
    cookies = os.path.join(REPO_ROOT, "cookies.txt")
    if os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout)
    except (subprocess.SubprocessError, OSError, ValueError, TypeError):
        return []
    return [e for e in (data.get("entries") or []) if e]


def _entry_meta(entry):
    return {"health": "ok", "title": entry.get("title"),
            "channel": entry.get("channel") or entry.get("uploader"),
            "view_count": entry.get("view_count"), "duration": entry.get("duration")}


def _best_yt_entry(artist, title, exclude=()):
    """Search YouTube for (artist, title), score hits with the review ranker, and
    return the highest-scoring entry not in `exclude` (and above the floor), else None."""
    import searcher  # repo-root script; heavy deps (rapidfuzz/pykakasi) load lazily
    query = " ".join(t for t in (artist, title) if t).strip()
    best, best_score = None, None
    for entry in _yt_search(query, settings.search_top_n()):
        yid = entry.get("id")
        if not yid or yid in exclude:
            continue
        try:
            s = searcher.score(entry, artist or "", title or "")
        except Exception:
            continue
        if best_score is None or s > best_score:
            best, best_score = entry, s
    if best is None or best_score < settings.yt_min_score():
        return None
    return best


def _track_terms(track):
    return (track.get("artist") or "", track.get("title") or "")


def _item_terms(item):
    """(artist, title) to search by for a Workspace item. Mirrors the frontend's
    itemAuthor/itemName fallback so a background find sees the SAME terms the picker
    does: item columns, then fetched metadata_json, then the linked track (joined in
    by list_workspace), then the file's tags. Reading columns only (missing the blob)
    left link-only items with empty terms, so they were skipped as unsearchable."""
    meta = _item_metadata(item)
    artist = item.get("channel") or meta.get("channel") or item.get("track_artist") or ""
    title = item.get("title") or meta.get("title") or item.get("track_title") or ""
    if not (artist or title) and item.get("relative_path"):
        # Read tags off the file itself (its own path), so out-of-folder staged files
        # (e.g. a download in e:\music\downloads) get their real artist/title too, not
        # only catalog-indexed files.
        tags = _read_audio_tags(os.path.join(item.get("folder_identity") or "", item["relative_path"]))
        artist, title = tags.get("tag_artist") or "", tags.get("tag_title") or ""
    if not title:
        # Last resort, matching the frontend's itemName: the file's own name (no
        # extension). A tagless local file (Cabo da Roca.m4a) is still searchable.
        stem = item.get("track_filename") or os.path.basename(item.get("relative_path") or "")
        title = os.path.splitext(stem)[0]
    return (artist, title)


def _item_has_valid_local(item):
    """A local file the item points to that still exists. Direct file refs are checked
    on disk (they may have been moved/renamed/deleted); track-linked files trust the
    catalog's availability flag (local_count)."""
    if item.get("folder_identity") and item.get("relative_path"):
        if os.path.isfile(os.path.join(item["folder_identity"], item["relative_path"])):
            return True
    return bool(item.get("local_count"))


def _item_needs_link(item):
    return (not item.get("youtube_id")
            or _item_metadata(item).get("health") in ("dead", "private"))


def _has_terms(item):
    """Anything to search by — stored metadata, a linked track, or file tags. Lets us
    re-find a link/file from whatever identity the item already carries (metadata, a
    downloaded video, or a local file), not only from a live local file."""
    return any(_item_terms(item))


def _best_local_record(artist, title):
    """Highest name-overlap catalog file for (artist, title), or None below the floor.
    ponytail: penalized partial-ratio over basenames; swap for tag/fingerprint if it misfires."""
    import searcher
    name = " ".join(t for t in (artist, title) if t).strip()
    if not name:
        return None
    best, best_score = None, None
    for rec in _CATALOG.records:
        s = searcher.penalized_partial_ratio(name, rec.get("basename") or "")
        if best_score is None or s > best_score:
            best, best_score = rec, s
    if best is None or best_score < settings.local_min_score():
        return None
    return best


class FindYoutube(BaseModel):
    track_id: PositiveInt


@app.post("/api/review/find-youtube")
def api_review_find_youtube(req: FindYoutube):
    """Find a *different* YouTube link for one track (Review's "find another"): search,
    skip rejected ids and the current one, apply the best hit as an unreviewed candidate."""
    conn = db.connect()
    try:
        track = conn.execute("SELECT id, artist, title, yt_id FROM tracks WHERE id=?",
                            (req.track_id,)).fetchone()
    finally:
        conn.close()
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    exclude = db.rejected_yt_ids(req.track_id) | ({track["yt_id"]} if track["yt_id"] else set())
    best = _best_yt_entry(track["artist"] or "", track["title"] or "", exclude)
    if best is None:
        raise HTTPException(status_code=404, detail="no new YouTube match found")
    try:
        with jobs.curation_write_guard():
            db.apply_track_yt(req.track_id, best["id"], _entry_meta(best))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return api_track(req.track_id)


class FindScope(BaseModel):
    ids: list[PositiveInt] | None = None


@app.post("/api/tasks/find-youtube/workspace")
def api_task_find_youtube_workspace(req: FindScope):
    """Background: for selected Workspace items with no live YouTube link but something
    to search by (metadata, a downloaded video, or a linked local file), auto-find and
    apply the best-scoring link (paced, cancellable)."""
    items = db.list_workspace()
    targets = [it for it in items if _item_needs_link(it) and _has_terms(it)]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in targets if it["id"] in wanted]
    by_id = {it["id"]: it for it in targets}

    def do_one(item_id):
        item = by_id[item_id]
        artist, title = _item_terms(item)
        exclude = db.rejected_yt_ids(item["track_id"]) if item.get("track_id") else set()
        best = _best_yt_entry(artist, title, exclude)
        if best is None:
            return False
        db.set_workspace_youtube(item_id, best["id"], _entry_meta(best))
        return True

    try:
        return tasks.run("workspace-find-youtube", "Find YouTube links",
                         list(by_id), do_one, delay=settings.task_delay(), noun="found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/tasks/find-local/workspace")
def api_task_find_local_workspace(req: FindScope):
    """Background: for selected Workspace items whose local file is missing (never had
    one, or it was moved/renamed/deleted) but that carry something to search by, auto-link
    the best name-matching catalog file. Local-only (no network pacing)."""
    _refresh_catalog()   # see files added since startup before matching
    items = db.list_workspace()
    targets = [it for it in items if not _item_has_valid_local(it) and _has_terms(it)]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in targets if it["id"] in wanted]
    by_id = {it["id"]: it for it in targets}

    def do_one(item_id):
        artist, title = _item_terms(by_id[item_id])
        rec = _best_local_record(artist, title)
        if rec is None:
            return False
        db.set_workspace_file(item_id, rec["folder_identity"], rec["relative_path"])
        return True

    try:
        return tasks.run("workspace-find-local", "Find local files",
                         list(by_id), do_one, delay=(0, 0), noun="found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── Lyrics + metadata finding (LRCLIB/community providers + MusicBrainz) ──────
# Lyrics reuse the repo-root lyrics_fetch providers; metadata is a small MusicBrainz
# recording lookup. Both fetch by (artist, title) — the SAME terms as find-youtube —
# store onto the item, and run as paced background sweeps (tasks.py) just like the
# link/file finders. See usb-ldac (web/api/{lyrics,metadata}.py) for the reference.
_MB_UA = "yt-playlist-generator/1.0 (personal music library)"


def _entity_path(item):
    """Absolute path of the local/track file a Workspace item points to, or None."""
    if item.get("relative_path"):
        p = os.path.join(item.get("folder_identity") or "", item["relative_path"])
        return p if os.path.isfile(p) else None
    rec = _record_for_track(item["track_id"]) if item.get("track_id") else None
    return _safe_delete_record(rec) if rec else None


def _read_sidecar(path):
    """Existing .lrc (synced) or .txt (plain) lyrics sidecar next to `path`, or None."""
    if not path:
        return None
    base = os.path.splitext(path)[0]
    for ext in (".lrc", ".txt"):
        side = base + ext
        if os.path.isfile(side):
            try:
                with open(side, encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return None
    return None


def _lyrics_payload(text):
    import lyrics_fetch
    text = text or ""
    return {"found": bool(text), "synced": lyrics_fetch.is_synced(text), "lyrics": text}


def _fetch_lyrics(artist, title):
    """Online lyrics for (artist, title) via the repo-root providers, '' on any miss.
    ponytail: providers swallow their own errors, so a network outage looks like 'no
    lyrics' rather than tripping the bulk cutoff — acceptable, they never fabricate."""
    import lyrics_fetch
    try:
        return lyrics_fetch.fetch_lyrics(artist, title) or ""
    except Exception:
        return ""


def _write_lyrics_sidecar(path, text):
    """Write a .lrc (synced) or .txt (plain) sidecar next to `path` and drop the stale
    alternate, so other tools (players, usb-ldac) see the fresh lyrics."""
    import lyrics_fetch
    out = lyrics_fetch.write_sidecar(path, text)
    alt = os.path.splitext(path)[0] + (".txt" if out.endswith(".lrc") else ".lrc")
    if os.path.isfile(alt):
        try:
            os.remove(alt)
        except OSError:
            pass
    return out


# ── entity dispatch (track | workspace): one code path, thin route wrappers ──────
# Lyrics + metadata + file-tags work the same for a Library track and a Workspace item
# (mirrors /embed). Per-kind differences live in these four tiny resolvers only.
_ENTITY_KINDS = ("track", "workspace")


def _entity_row(kind, entity_id):
    if kind not in _ENTITY_KINDS:
        raise HTTPException(status_code=404, detail="unknown entity kind")
    if kind == "workspace":
        return _ws_item(entity_id)
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id=?", (entity_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    return db._expand_extra(dict(row))


def _entity_terms(kind, row):
    if kind == "workspace":
        return _item_terms(row)
    return (row.get("artist") or "", row.get("title") or "")


def _entity_file_path(kind, row):
    if kind == "workspace":
        return _entity_path(row)
    rec = _record_for_track(row["id"])
    return _safe_delete_record(rec) if rec else None


def _entity_stored_lyrics(kind, row):
    # Workspace keeps a lyrics blob in metadata_json; a track relies on its sidecar.
    return _item_metadata(row).get("lyrics") if kind == "workspace" else None


def _entity_store_lyrics(kind, entity_id, path, text):
    if kind == "workspace":
        db.set_workspace_metadata(entity_id, {"lyrics": text})
    if path:
        try:
            _write_lyrics_sidecar(path, text)
        except OSError:
            pass


def _entity_lyrics_get(kind, entity_id, force=False):
    """Lyrics for an entity: stored blob -> sidecar -> online. Stores what it finds.
    `force` re-fetches online even when something is already stored."""
    row = _entity_row(kind, entity_id)
    path = _entity_file_path(kind, row)
    if not force:
        stored = _entity_stored_lyrics(kind, row)
        if stored:
            return _lyrics_payload(stored)
        side = _read_sidecar(path)
        if side:
            _entity_store_lyrics(kind, entity_id, None, side)   # cache blob; sidecar already on disk
            return _lyrics_payload(side)
    artist, title = _entity_terms(kind, row)
    text = _fetch_lyrics(artist, title)
    if text:
        _entity_store_lyrics(kind, entity_id, path, text)
    return _lyrics_payload(text)


def _entity_lyrics_save(kind, entity_id, text):
    """Persist user-edited lyrics: metadata blob (workspace) + sidecar (either kind)."""
    row = _entity_row(kind, entity_id)
    path = _entity_file_path(kind, row)
    text = (text or "").strip()
    if kind == "workspace":
        db.set_workspace_metadata(entity_id, {"lyrics": text})
    elif not path:
        raise HTTPException(status_code=400, detail="this track has no local file to save lyrics to")
    if path and text:
        try:
            _write_lyrics_sidecar(path, text)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"could not write sidecar: {e}")
    return _lyrics_payload(text)


def _entity_apply_metadata(kind, entity_id):
    """Auto-apply the best confident MusicBrainz artist/title. Returns the match dict
    when applied (above MB_MIN_SCORE), else None. Reversible via Info."""
    row = _entity_row(kind, entity_id)
    artist, title = _entity_terms(kind, row)
    best = _mb_best(artist, title)
    if not best or best["score"] < settings.mb_min_score():
        return None
    if kind == "workspace":
        db.set_workspace_metadata(entity_id, {"title": best["title"], "channel": best["artist"],
                                              "mb_score": best["score"]})
    else:
        db.update_track_fields(entity_id, {"artist": best["artist"], "title": best["title"]})
    return best


def _entity_file_tags(kind, entity_id):
    """Artist/title/album read from the entity's actual audio file (mutagen), plus
    whether a readable file was found. Answers 'what's in the file?' for the UI."""
    row = _entity_row(kind, entity_id)
    path = _entity_file_path(kind, row)
    return {"has_file": bool(path), **(_read_audio_tags(path) if path else {})}


def _mb_best(artist, title):
    """Best MusicBrainz recording match for (artist, title): {artist,title,score} or
    None below/absent. Raises tasks.NetworkDown on a fetch failure so a bulk sweep can
    trip its network cutoff instead of silently overwriting nothing."""
    import urllib.parse
    import urllib.request
    terms = []
    for field, value in (("artist", artist), ("recording", title)):
        value = (value or "").strip().replace('"', "")
        if value:
            terms.append(f'{field}:"{value}"')
    if not terms:
        return None
    params = urllib.parse.urlencode({"query": " AND ".join(terms), "fmt": "json",
                                     "limit": settings.mb_search_limit()})
    req = urllib.request.Request("https://musicbrainz.org/ws/2/recording/?" + params,
                                 headers={"User-Agent": _MB_UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read()).get("recordings", [])
    except Exception as e:
        raise tasks.NetworkDown() from e
    if not rows:
        return None
    best = max(rows, key=lambda r: r.get("score") or 0)
    artist_name = "".join(f"{p.get('name', '')}{p.get('joinphrase', '')}"
                          for p in best.get("artist-credit") or [])
    return {"artist": artist_name, "title": best.get("title") or "", "score": best.get("score") or 0}


def _ws_item(item_id):
    item = next((it for it in db.list_workspace() if it["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="workspace item not found")
    return item


class LyricsSave(BaseModel):
    lyrics: str = ""


# Generic {kind} routes (kind = 'track' | 'workspace'); one implementation, both entities.
@app.get("/api/{kind}/{entity_id}/lyrics")
def api_entity_lyrics(kind: str, entity_id: int, refresh: bool = False):
    return _entity_lyrics_get(kind, entity_id, force=refresh)


@app.post("/api/{kind}/{entity_id}/lyrics")
def api_entity_find_lyrics(kind: str, entity_id: int):
    """Find + store lyrics for one entity ('Find lyrics')."""
    return _entity_lyrics_get(kind, entity_id, force=True)


@app.post("/api/{kind}/{entity_id}/lyrics/save")
def api_entity_save_lyrics(kind: str, entity_id: int, req: LyricsSave):
    """Persist user-edited lyrics (the lyric viewer's Edit → Save)."""
    return _entity_lyrics_save(kind, entity_id, req.lyrics)


@app.post("/api/{kind}/{entity_id}/find-metadata")
def api_entity_find_metadata(kind: str, entity_id: int):
    """Auto-apply the best confident MusicBrainz match for one entity ('Find metadata')."""
    try:
        best = _entity_apply_metadata(kind, entity_id)
    except tasks.NetworkDown:
        raise HTTPException(status_code=502, detail="MusicBrainz unreachable")
    if best is None:
        raise HTTPException(status_code=404, detail="no confident MusicBrainz match")
    return _decorate_workspace_items([_ws_item(entity_id)])[0] if kind == "workspace" else api_track(entity_id)


@app.get("/api/{kind}/{entity_id}/file-tags")
def api_entity_file_tags(kind: str, entity_id: int):
    return _entity_file_tags(kind, entity_id)


# ── Per-row label verification (YouTube / Local file / Downloaded labels) ────
def _entity_yt_id(kind, row):
    return row.get("youtube_id") if kind == "workspace" else row.get("yt_id")


def _entity_local_ref(kind, row):
    """(folder_identity, relative_path) of the entity's local file from the DB (independent of
    whether the file still exists), or None. Direct Workspace ref wins; else the track's link."""
    if kind == "workspace" and row.get("relative_path") and not _is_download_ref(row):
        return row["folder_identity"], row["relative_path"]
    track_id = row.get("track_id") if kind == "workspace" else row.get("id")
    if track_id:
        links = db.local_deletion_links([track_id])
        if links:
            return links[0]["folder_identity"], links[0]["relative_path"]
    return None


@app.post("/api/{kind}/{entity_id}/verify-link")
def api_entity_verify_link(kind: str, entity_id: int):
    """Re-check one YouTube link's health (yt-dlp). Persists it; a dead/private link on an
    approved track sends the track back to unreviewed (db enforces the rule)."""
    row = _entity_row(kind, entity_id)
    yt_id = _entity_yt_id(kind, row)
    if not yt_id:
        raise HTTPException(status_code=400, detail="no YouTube link to verify")
    meta = _resolve_yt_metadata(yt_id)
    health = meta.get("health", "unknown")
    if kind == "workspace":
        db.set_workspace_metadata(entity_id, meta)
        db.unreview_track_if_dead(row.get("track_id"), health)
    else:
        db.set_track_health(entity_id, health)
    return {"health": health}


def _verify_entity_local(kind, entity_id):
    """Core of verify-local (no catalog refresh — callers refresh once): does the entity's local
    file still exist on disk? If not, flag its track link unavailable AND clear a Workspace item's
    own dangling direct ref (mark_links_unavailable can't, it isn't a track link), so the 'local'
    label drops for both. Returns present. Shared by the per-row route and the bulk sweep."""
    row = _entity_row(kind, entity_id)
    ref = _entity_local_ref(kind, row)
    if not ref:
        return False
    folder_identity, relative_path = ref
    present = bool(relative_path and os.path.isfile(os.path.join(folder_identity or "", relative_path)))
    if not present:
        db.mark_links_unavailable(folder_identity, relative_path)
        if kind == "workspace" and row.get("relative_path") and not _is_download_ref(row):
            db.clear_workspace_file_ref_or_remove(entity_id)
    return present


@app.post("/api/{kind}/{entity_id}/verify-local")
def api_entity_verify_local(kind: str, entity_id: int):
    """Re-check the entity's local mp3-folder file still exists on disk; clear the 'local' label
    if it's gone. Refreshes the catalog first."""
    _refresh_catalog()
    return {"present": _verify_entity_local(kind, entity_id)}


@app.post("/api/{kind}/{entity_id}/verify-download")
def api_entity_verify_download(kind: str, entity_id: int):
    """Re-check the download-folder file for this entity's id still exists (the 'downloaded'
    label is derived live, so the caller just reloads to refresh it)."""
    row = _entity_row(kind, entity_id)
    yt_id = _entity_yt_id(kind, row)
    return {"present": bool(yt_id and _download_file_path(yt_id))}


class RomanizeReq(BaseModel):
    texts: list[str]


@app.post("/api/romanize")
def api_romanize(req: RomanizeReq):
    """Romanize each string (CJK → Hepburn, ASCII untouched). Lyrics send one blob
    (LRC-safe); metadata edit sends its field values. Filenames use
    /api/romanize/filename (it renames the file, not just the text)."""
    import romanize
    return {"texts": [romanize.romanize_text(t) for t in req.texts]}


@app.post("/api/tasks/find-lyrics/workspace")
def api_task_find_lyrics_workspace(req: FindScope):
    """Background: fetch + store lyrics for selected Workspace items missing them
    (paced, cancellable). Items that already have stored lyrics are skipped."""
    items = db.list_workspace()
    targets = [it for it in items if _has_terms(it) and not _item_metadata(it).get("lyrics")]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in targets if it["id"] in wanted]
    ids = [it["id"] for it in targets]

    def do_one(item_id):   # ponytail: re-lists workspace per item (O(n²)); fine, sweep is paced
        return _entity_lyrics_get("workspace", item_id, force=False)["found"]

    try:
        return tasks.run("workspace-find-lyrics", "Find lyrics", ids, do_one,
                         delay=settings.task_delay(), noun="found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/tasks/find-metadata/workspace")
def api_task_find_metadata_workspace(req: FindScope):
    """Background: for selected Workspace items with something to search by, auto-apply
    the best confident MusicBrainz artist/title (paced, cancellable). Overwrites
    title/channel above the score floor — reversible via Info edit."""
    items = db.list_workspace()
    targets = [it for it in items if _has_terms(it)]
    if req.ids is not None:
        wanted = set(req.ids)
        targets = [it for it in targets if it["id"] in wanted]
    ids = [it["id"] for it in targets]

    def do_one(item_id):
        return _entity_apply_metadata("workspace", item_id) is not None

    try:
        return tasks.run("workspace-find-metadata", "Find metadata", ids, do_one,
                         delay=settings.task_delay(), noun="updated")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── interactive search pickers (top-N ranked, user chooses) ──────────────────
# Unlike the auto-apply tasks above, these return a ranked candidate LIST (with the
# same review score shown) so the user picks in a modal, then applies their choice.
_YT_ID_RE = re.compile(r"[a-zA-Z0-9_-]{11}")


class YoutubeSearch(BaseModel):
    query: str
    artist: str = ""          # scoring target (stays fixed while the user edits `query`)
    title: str = ""
    limit: int | None = None  # None -> Settings' SEARCH_RESULT_LIMIT


@app.post("/api/search/youtube")
def api_search_youtube(req: YoutubeSearch):
    """Top YouTube hits for `query`, each scored by the review ranker against
    (artist, title) and returned high-to-low. Network."""
    import searcher
    limit = settings.search_result_limit() if req.limit is None else max(1, min(req.limit, 50))
    out = []
    for e in _yt_search(req.query, limit):
        yid = e.get("id")
        if not yid:
            continue
        try:
            s = searcher.score(e, req.artist or "", req.title or "")
        except Exception:
            s = 0
        out.append({"id": yid, "title": e.get("title"),
                    "channel": e.get("channel") or e.get("uploader"),
                    "view_count": e.get("view_count"), "duration": e.get("duration"),
                    "score": round(s)})
    out.sort(key=lambda r: r["score"], reverse=True)
    return {"results": out[:limit]}


class LocalSearch(BaseModel):
    query: str
    limit: int | None = None  # None -> Settings' SEARCH_RESULT_LIMIT


@app.post("/api/search/local")
def api_search_local(req: LocalSearch):
    """Top catalog files for `query` by name similarity (same primitive as the review
    ranker), high-to-low. Local-only."""
    import searcher
    _refresh_catalog()   # pick up files added since startup (e.g. a just-downloaded track)
    q = (req.query or "").strip()
    limit = settings.search_result_limit() if req.limit is None else max(1, min(req.limit, 50))
    scored = sorted(
        ((searcher.penalized_partial_ratio(q, rec.get("basename") or ""), rec)
         for rec in _CATALOG.records),
        key=lambda t: t[0], reverse=True)
    results = [{"folder_identity": rec["folder_identity"], "relative_path": rec["relative_path"],
                "basename": rec["basename"], "score": round(s)} for s, rec in scored[:limit]]
    return {"results": results}


class ApplyYoutube(BaseModel):
    youtube_id: str
    title: str | None = None
    channel: str | None = None
    view_count: int | None = None


def _validate_yt_id(youtube_id):
    if not _YT_ID_RE.fullmatch(youtube_id or ""):
        raise HTTPException(status_code=400, detail="invalid YouTube id")


@app.post("/api/track/{track_id}/youtube")
def api_track_set_youtube(track_id: int, req: ApplyYoutube):
    """Apply a user-chosen YouTube link to a Library track (unreviewed candidate)."""
    _validate_yt_id(req.youtube_id)
    try:
        with jobs.curation_write_guard():
            db.apply_track_yt(track_id, req.youtube_id,
                              {"title": req.title, "channel": req.channel, "view_count": req.view_count})
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return api_track(track_id)


@app.post("/api/workspace/{item_id}/youtube")
def api_workspace_set_youtube(item_id: int, req: ApplyYoutube):
    """Apply a user-chosen YouTube link to a Workspace item."""
    _validate_yt_id(req.youtube_id)
    try:
        db.set_workspace_youtube(item_id, req.youtube_id,
                                 {"title": req.title, "channel": req.channel, "view_count": req.view_count})
    except KeyError:
        raise HTTPException(status_code=404, detail="workspace item not found")
    return {"ok": True}


class ApplyLocal(BaseModel):
    folder_identity: str | None = None
    relative_path: str | None = None
    path: str | None = None          # absolute path (Pick local file) — may be outside folders


def _resolve_apply_local(req):
    """Ref for a file to link: an absolute `path` (registers it as staged so it stays
    playable/revealable) or a catalog/staged (folder_identity, relative_path). None if
    it doesn't resolve to an existing file."""
    if req.path:
        ref = _ref_for_path(req.path)
        if ref and not _is_file_tracked(ref["folder_identity"], ref["relative_path"]) and not any(
                s["folder_identity"] == ref["folder_identity"] and s["relative_path"] == ref["relative_path"]
                for s in _STAGED_FILES):
            _STAGED_FILES.append(ref)
        return ref
    return _untracked_ref(req.folder_identity, req.relative_path)


@app.post("/api/track/{track_id}/local-file")
def api_track_set_local(track_id: int, req: ApplyLocal):
    """Link a user-chosen local file (catalog, staged, or a picked absolute path) to a track."""
    got = _resolve_apply_local(req)
    if got is None:
        raise HTTPException(status_code=404, detail="file missing")
    try:
        with jobs.curation_write_guard():
            db.link_file_to_track(track_id, got["folder_identity"], got["relative_path"],
                                  got["file_size"], got["modified_at"])
    except KeyError:
        raise HTTPException(status_code=404, detail="track not found")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    return api_track(track_id)


@app.post("/api/workspace/{item_id}/local-file")
def api_workspace_set_local(item_id: int, req: ApplyLocal):
    """Link a user-chosen local file (catalog, staged, or a picked absolute path) to an item."""
    got = _resolve_apply_local(req)
    if got is None:
        raise HTTPException(status_code=404, detail="file missing")
    try:
        db.set_workspace_file(item_id, got["folder_identity"], got["relative_path"])
    except KeyError:
        raise HTTPException(status_code=404, detail="workspace item not found")
    return {"ok": True}


def _parse_yt_id(value):
    """Extract an 11-char YouTube id from a raw id or any watch/share/embed/shorts URL."""
    value = (value or "").strip()
    if _YT_ID_RE.fullmatch(value):
        return value
    m = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/|/live/)([a-zA-Z0-9_-]{11})", value)
    return m.group(1) if m else None


class ResolveYoutube(BaseModel):
    value: str                       # pasted id or URL
    artist: str = ""
    title: str = ""


@app.post("/api/resolve/youtube")
def api_resolve_youtube(req: ResolveYoutube):
    """Verify a pasted id/URL: resolve health via yt-dlp and score it against the row's
    (artist, title), so the UI can show the match before the user confirms. Network."""
    import searcher
    yid = _parse_yt_id(req.value)
    if not yid:
        raise HTTPException(status_code=400, detail="not a valid YouTube id or URL")
    meta = _resolve_yt_metadata(yid)
    entry = {"id": yid, "title": meta.get("title"), "uploader": meta.get("channel"),
             "view_count": meta.get("view_count")}
    try:
        score = round(searcher.score(entry, req.artist or "", req.title or ""))
    except Exception:
        score = 0
    return {"id": yid, "alive": meta.get("health") == "ok", "health": meta.get("health"),
            "title": meta.get("title"), "channel": meta.get("channel"),
            "view_count": meta.get("view_count"), "score": score}


class ScoreLocal(BaseModel):
    path: str
    artist: str = ""
    title: str = ""


@app.post("/api/score/local")
def api_score_local(req: ScoreLocal):
    """Verify a picked absolute path exists and score its name against (artist, title),
    so the UI can show the match before the user confirms setting it. Local-only."""
    import searcher
    ref = _ref_for_path(req.path)
    if ref is None:
        raise HTTPException(status_code=404, detail="file not found")
    target = " ".join(t for t in (req.artist, req.title) if t).strip()
    score = round(searcher.penalized_partial_ratio(target, ref["basename"])) if target else 0
    return {"exists": True, "basename": ref["basename"], "path": os.path.abspath(req.path),
            "folder_identity": ref["folder_identity"], "relative_path": ref["relative_path"],
            "score": score}


class FieldPatch(BaseModel):
    fields: dict


@app.patch("/api/track/{track_id}")
def api_track_patch(track_id: int, req: FieldPatch):
    """Edit whitelisted Library-track metadata (Info modal). Non-editable columns and the
    file identity are ignored; a set yt_id must be a valid 11-char id."""
    yt = req.fields.get("yt_id")
    if yt and not _YT_ID_RE.fullmatch(yt):
        raise HTTPException(status_code=400, detail="invalid yt_id")
    try:
        with jobs.curation_write_guard():
            db.update_track_fields(track_id, req.fields)
    except ValueError:
        raise HTTPException(status_code=400, detail="no editable fields")
    except KeyError:
        raise HTTPException(status_code=404, detail="track not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return api_track(track_id)


@app.patch("/api/workspace/{item_id}")
def api_workspace_patch(item_id: int, req: FieldPatch):
    """Edit whitelisted Workspace-item metadata (Info modal)."""
    try:
        db.update_workspace_fields(item_id, req.fields)
    except ValueError:
        raise HTTPException(status_code=400, detail="no editable fields")
    except KeyError:
        raise HTTPException(status_code=404, detail="workspace item not found")
    return {"ok": True}


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
    _DELETE_TOKENS[token] = {"kind": kind, "expires": time.time() + settings.delete_token_ttl(), **payload}
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
    return {"token": token, "expires_in": settings.delete_token_ttl(),
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


# ── Workspace bulk delete-local ─────────────────────────────────────────────
# Deletes the mp3-folder local file behind selected Workspace items. Unlike the
# Library flow it is NOT gated on approval (the user curates in the Workspace), but
# every other safeguard stays: only files contained in a configured mp3 folder are
# touched (download-folder + out-of-folder refs are skipped), behind a preview →
# short-lived token → typed DELETE → stat-revalidation → audit.
def _item_delete_ref(item):
    """(folder_identity, relative_path) of a Workspace item's deletable mp3-folder local
    file, or None. Prefers the item's own direct file ref (when it is an mp3-folder file,
    not a download); else the linked track's available local file."""
    if item.get("relative_path") and not _is_download_ref(item):
        record = _record_for_ref(item["folder_identity"], item["relative_path"])
        return (record["folder_identity"], record["relative_path"]) if record else None
    if item.get("track_id"):
        for row in db.local_deletion_links([item["track_id"]]):
            record = _record_for_ref(row["folder_identity"], row["relative_path"])
            if record:
                return record["folder_identity"], record["relative_path"]
    return None


def _workspace_item_refs(ids):
    """Resolve item ids -> deletable refs. Also returns `direct_ids`: items that delete their
    OWN direct file ref (so their now-dangling ref must be cleared after deletion — a track-
    linked item instead just loses its link's availability, handled in the delete itself)."""
    by_id = {item["id"]: item for item in db.list_workspace()}
    refs, skipped, direct_ids = [], [], []
    for item_id in ids:
        item = by_id.get(item_id)
        if item is None:
            skipped.append({"id": item_id, "reason": "not found"})
            continue
        ref = _item_delete_ref(item)
        if ref:
            refs.append(ref)
            if item.get("relative_path") and not _is_download_ref(item):
                direct_ids.append(item_id)
        else:
            skipped.append({"id": item_id, "reason": "no deletable mp3-folder file"})
    return refs, skipped, direct_ids


def _ref_delete_targets(refs):
    """Safe delete targets from (folder_identity, relative_path) pairs — contained mp3-folder
    files only, no approval gate. Deduped (two items can share one file)."""
    targets, seen = [], set()
    for folder_identity, relative_path in refs:
        key = (os.path.normcase(folder_identity or ""), relative_path)
        if key in seen:
            continue
        seen.add(key)
        record = _record_for_ref(folder_identity, relative_path)
        path = _safe_delete_record(record) if record is not None else False
        if not path:
            raise HTTPException(status_code=409, detail=f"local file missing or unsafe: {relative_path}")
        info = os.stat(path)
        targets.append({"track_id": None, "folder_identity": record["folder_identity"],
                        "relative_path": record["relative_path"], "file_size": info.st_size,
                        "modified_at": str(info.st_mtime_ns), "identity": (info.st_dev, info.st_ino),
                        "record": record, "delete_path": path})
    return targets


@app.post("/api/workspace/local-delete/preview")
def api_workspace_local_delete_preview(req: WorkspaceLocalDelete):
    refs, skipped, _ = _workspace_item_refs(req.ids)
    targets = _ref_delete_targets(refs)
    state = _target_state(targets)
    token = _new_token("workspace-local-delete",
                       {"ids": list(req.ids), "state": state,
                        "refs": [[t["folder_identity"], t["relative_path"]] for t in state]})
    return {"token": token, "expires_in": settings.delete_token_ttl(),
            "targets": [{k: item[k] for k in ("folder_identity", "relative_path", "file_size", "modified_at")}
                        for item in state],
            "skipped": skipped}


@app.post("/api/workspace/local-delete")
def api_workspace_local_delete(req: WorkspaceLocalDeleteConfirm):
    if req.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="type DELETE to confirm")
    data = _take_token(req.token, "workspace-local-delete")
    if list(req.ids) != data["ids"]:
        raise HTTPException(status_code=409, detail="selection does not match preview")
    # Which selected items delete their OWN file ref — compute now, while the files still exist.
    _, _, direct_ids = _workspace_item_refs(req.ids)
    try:
        jobs.reserve_pipeline("workspace_local_delete")
        with jobs.curation_write_guard():
            result = _perform_ref_delete(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    finally:
        jobs.release_pipeline("workspace_local_delete")
    # Delete succeeded: drop the now-dangling direct file refs (or remove file-only items).
    for item_id in direct_ids:
        db.clear_workspace_file_ref_or_remove(item_id)
    return result


def _perform_ref_delete(data):
    refs = [tuple(ref) for ref in data["refs"]]
    try:
        targets = _ref_delete_targets(refs)
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
        for target in deleted:                     # a deleted file invalidates any track link to it
            db.mark_links_unavailable(target["folder_identity"], target["relative_path"])
        db.finish_local_deletions(
            [{**target, "outcome": "deleted"} for target in deleted] +
            [{**target, "outcome": "rejected", "detail": str(exc)}
             for target in targets if target not in deleted])
        try:
            _install_catalog(_build_file_catalog(settings.configured_mp3_folders()))
        finally:
            raise HTTPException(status_code=409, detail="delete changed during confirmation")
    for target in targets:                          # track links to a deleted file -> unavailable
        db.mark_links_unavailable(target["folder_identity"], target["relative_path"])
    db.finish_local_deletions([{**target, "outcome": "deleted"} for target in targets])
    _install_catalog(_build_file_catalog(settings.configured_mp3_folders()))
    return {"deleted": [{"folder_identity": target["folder_identity"], "relative_path": target["relative_path"]}
                        for target in targets]}


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
    """Turn untracked files (catalog or staged out-of-folder) into Library entries
    (find-or-create a track, then link the file). Bulk; returns a per-file result."""
    results = []
    try:
        with jobs.curation_write_guard():
            for ref in req.files:
                got = _untracked_ref(ref.folder_identity, ref.relative_path)
                if got is None:
                    results.append({"relative_path": ref.relative_path, "added": False,
                                    "reason": "file missing"})
                    continue
                try:
                    out = db.add_local_file_to_library(
                        got["folder_identity"], got["relative_path"], got["basename"],
                        got["file_size"], got["modified_at"])
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
                got = _untracked_ref(ref.folder_identity, ref.relative_path)
                if got is None:
                    results.append({"relative_path": ref.relative_path, "added": False, "reason": "file missing"})
                    continue
                item = db.add_workspace_file(got["folder_identity"], got["relative_path"])   # title null -> tags/basename win
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


def _download_files_for_id(yt_id):
    """Every download-folder file carrying this id (safe/contained). A format change
    leaves the old + new file side by side, so replace needs the full set, not just one."""
    out = []
    try:
        root = _safe_download_root()
    except HTTPException:
        return out
    if not root:
        return out
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if _download_id(name) != yt_id:
            continue
        path = os.path.normpath(os.path.join(root, name))
        try:
            if os.path.commonpath((root, path)) == root and not _is_reparse_point(path) and os.path.isfile(path):
                out.append(path)
        except (OSError, ValueError):
            pass
    return out


def _remove_stale_after_replace(pre_files):
    """After a successful replace-download, drop each id's pre-run files ONLY if a genuinely
    new file landed (e.g. codec changed opus->mp3). Same-name overwrite or a failed id keeps
    its old file — so a failed download never loses the previous copy."""
    for yt_id, old in pre_files.items():
        if set(_download_files_for_id(yt_id)) - old:
            for path in old:
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass


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
        if not path and req.folder_identity and req.relative_path:
            ref = _untracked_ref(req.folder_identity, req.relative_path)   # staged out-of-folder file
            path = ref["path"] if ref else None
    if not path:
        raise HTTPException(status_code=404, detail="file missing or unsafe")
    # explorer.exe parses its own command line; the list form mis-parses and it
    # falls back to Documents. Pass one quoted string (Windows CreateProcess).
    try:
        subprocess.run('explorer /select,"%s"' % os.path.normpath(path), timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
    return {"ok": True}


def _local_file_abs_path(folder_identity, relative_path):
    """Absolute path of a linked local file, validated. Covers catalog identities
    (normcased) and OS-picker identities (raw case, from _ref_for_path): try the shared
    resolver first, then fall back to a direct join contained in a configured folder."""
    ref = _untracked_ref(folder_identity, relative_path)
    if ref:
        return ref["path"]
    p = os.path.join(folder_identity or "", relative_path or "")
    if not (relative_path and os.path.isfile(p)) or _is_reparse_point(p):
        return None
    rp = os.path.normcase(os.path.realpath(p))
    for folder in settings.configured_mp3_folders():
        root = os.path.normcase(os.path.realpath(folder))
        try:
            if os.path.commonpath((root, rp)) == root:
                return p
        except ValueError:
            continue
    return None


def _romanized_basename(name):
    """Romanized filename (stem converted, extension kept), or None when it is already
    ASCII / unchanged. Strips characters illegal in a Windows filename."""
    import romanize
    stem, ext = os.path.splitext(name)
    new = re.sub(r'[\\/:*?"<>|]', "_", romanize.romanize_text(stem)).strip()
    return (new + ext) if new and new != stem else None


def _rename_sidecars(old_path, new_path):
    """Rename any .lrc/.txt lyric sidecar alongside a renamed audio file so it stays paired."""
    old_stem, new_stem = os.path.splitext(old_path)[0], os.path.splitext(new_path)[0]
    for ext in (".lrc", ".txt"):
        src = old_stem + ext
        if os.path.isfile(src):
            try:
                os.replace(src, new_stem + ext)
            except OSError:
                pass


@app.post("/api/romanize/filename")
def api_romanize_filename(req: RevealRef):
    """Rename the resolved file in place so its name is romanized (CJK → Latin), then
    repoint the DB refs (track link, Workspace ref, tracks.filename) + lyric sidecars and
    rebuild the catalog. Download files are renamed only — they're located by [id], which
    is ASCII and survives. Same locator shape as /api/reveal."""
    is_download = req.download_yt_id is not None
    folder_identity = relative_path = None
    if is_download:
        path = _download_file_path(req.download_yt_id)
    else:
        if req.track_id is not None:
            links = db.local_deletion_links([req.track_id])
            link = links[0] if links else None
            if link:
                folder_identity, relative_path = link["folder_identity"], link["relative_path"]
        elif req.folder_identity and req.relative_path:
            folder_identity, relative_path = req.folder_identity, req.relative_path
        path = _local_file_abs_path(folder_identity, relative_path) if folder_identity else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing or unsafe")
    newbase = _romanized_basename(os.path.basename(path))
    if not newbase:
        return {"renamed": False, "name": os.path.basename(path)}
    newpath = os.path.join(os.path.dirname(path), newbase)
    if os.path.exists(newpath):
        raise HTTPException(status_code=409, detail="a file with the romanized name already exists")
    os.rename(path, newpath)
    _rename_sidecars(path, newpath)
    if not is_download and folder_identity and relative_path:
        new_rel = (relative_path.rsplit("/", 1)[0] + "/" + newbase) if "/" in relative_path else newbase
        db.rename_file_link(folder_identity, relative_path, new_rel,
                            os.path.basename(relative_path), newbase)
        _install_catalog(_build_file_catalog(settings.configured_mp3_folders()))
    return {"renamed": True, "name": newbase}


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
    """Serve an untracked file read-only (preview): a catalog file (containment + reparse
    validated) or an explicitly-staged out-of-folder file the user picked."""
    ref = _untracked_ref(folder_identity, relative_path)
    if ref is None:
        raise HTTPException(status_code=404, detail="local file missing or unsafe")
    path = ref["path"]
    media_type = _AUDIO_MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


def _embed_file_path(*, relative_path=None, folder_identity=None, is_download=False,
                     youtube_id=None, track_id=None, source="local"):
    """Resolve which file an "Embed metadata" action writes into. `source` picks the
    file for a row that has both: 'download' -> the download-folder file (the item's own
    ref if it lives there, else the id-named download); 'local' -> the mp3-folder catalog
    file (or a staged/untracked file's own path)."""
    if source == "download":
        if relative_path:
            p = os.path.join(folder_identity or "", relative_path)
            if os.path.isfile(p):
                return p
        return _download_file_path(youtube_id) if youtube_id else None
    if relative_path and not is_download:
        ref = _untracked_ref(folder_identity, relative_path)
        if ref:
            return ref["path"]
    if track_id:
        record = _record_for_track(track_id)
        return _safe_delete_record(record) if record is not None else None
    return None


def _embed_metadata_into(path, artist, title):
    """Write our best-known artist/title into the file's tags (mutagen easy mode, so only
    widely-supported tags). Overwrites those two fields; leaves the rest untouched."""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file to embed into not found")
    if not (artist or title):
        raise HTTPException(status_code=400, detail="no metadata to embed")
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=True)
        if audio is None:
            raise HTTPException(status_code=415, detail="unsupported audio format")
        if audio.tags is None:
            audio.add_tags()
        if artist:
            audio["artist"] = artist
        if title:
            audio["title"] = title
        audio.save()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"embed failed: {e}")
    return {"ok": True, "path": path, "artist": artist, "title": title}


class EmbedReq(BaseModel):
    source: str = "local"   # 'local' | 'download'


@app.post("/api/workspace/{item_id}/embed")
def api_workspace_embed(item_id: int, req: EmbedReq):
    """Embed our metadata into a Workspace item's local or downloaded file."""
    item = next((it for it in db.list_workspace() if it["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="workspace item not found")
    artist, title = _item_terms(item)
    path = _embed_file_path(relative_path=item.get("relative_path"),
                            folder_identity=item.get("folder_identity"),
                            is_download=_is_download_ref(item),
                            youtube_id=item.get("youtube_id"),
                            track_id=item.get("track_id"), source=req.source)
    return _embed_metadata_into(path, artist, title)


@app.post("/api/track/{track_id}/embed")
def api_track_embed(track_id: int, req: EmbedReq):
    """Embed a Library track's metadata into its local (or downloaded) file."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT artist, title, yt_title, yt_channel, yt_id "
                           "FROM tracks WHERE id=?", (track_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="track not found")
    artist = row["artist"] or row["yt_channel"] or ""
    title = row["title"] or row["yt_title"] or ""
    path = _embed_file_path(youtube_id=row["yt_id"], track_id=track_id, source=req.source)
    return _embed_metadata_into(path, artist, title)


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


# Files picked from anywhere on disk and added by absolute path (no copy). Import's
# staged list lives here in process memory: it survives a browser refresh but not a
# server restart, which is exactly what Import wants (Workspace/Library persist in SQLite).
_STAGED_FILES = []      # ponytail: process memory by design; a set/db table if it must outlive restarts


def _ref_for_path(path):
    """A file ref for any absolute path (folder identity = its own dir, relative path =
    its basename). None if the path is not an existing file. No containment requirement:
    files may live outside the configured mp3 folders — referenced in place, never copied."""
    try:
        p = os.path.abspath(path)
        if not os.path.isfile(p):
            return None
        st = os.stat(p)
    except OSError:
        return None
    return {"folder_identity": os.path.dirname(p), "relative_path": os.path.basename(p),
            "basename": os.path.basename(p), "file_size": st.st_size,
            "modified_at": str(st.st_mtime_ns), "media_type": "audio",
            "category": "unmatched", "tracks": []}


def _is_file_tracked(folder_identity, relative_path):
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT 1 FROM track_file_links WHERE folder_identity=? AND relative_path=? "
            "AND available=1 LIMIT 1", (folder_identity, relative_path)).fetchone() is not None
    finally:
        conn.close()


def _staged_path(folder_identity, relative_path):
    """Absolute path of an explicitly picked (staged) out-of-folder file, or None. These
    bypass the mp3-folder containment guard precisely because the user hand-picked them:
    identity must still match a live _STAGED_FILES entry and the file must exist."""
    for s in _STAGED_FILES:
        if s["folder_identity"] == folder_identity and s["relative_path"] == relative_path:
            p = os.path.join(folder_identity, relative_path)
            return p if os.path.isfile(p) else None
    return None


def _untracked_ref(folder_identity, relative_path):
    """Resolve an untracked file to a safe abs path + identity — whether it's a catalog
    file (containment-checked) or an explicitly-staged out-of-folder file. None if it
    resolves nowhere or is missing. One resolver so staged files are first-class in
    play/reveal/add, exactly like in-folder untracked files."""
    record = _record_for_ref(folder_identity, relative_path)
    if record is not None:
        path = _safe_delete_record(record)
        return {"folder_identity": record["folder_identity"], "relative_path": record["relative_path"],
                "basename": record["basename"], "file_size": record.get("file_size"),
                "modified_at": record.get("modified_at"), "path": path} if path else None
    path = _staged_path(folder_identity, relative_path)
    if not path:
        return None
    st = os.stat(path)
    return {"folder_identity": folder_identity, "relative_path": relative_path,
            "basename": os.path.basename(path), "file_size": st.st_size,
            "modified_at": str(st.st_mtime_ns), "path": path}


class AddFilesByPath(BaseModel):
    paths: list[str]
    target: str        # 'library' | 'workspace' | 'untracked'


@app.post("/api/files/add")
def api_files_add(req: AddFilesByPath):
    """Add absolute-path files to one destination (DRY entry point for every screen's
    file picker): 'library' (find-or-create a track), 'workspace' (file-only item), or
    'untracked' (Import's in-memory staged list, untracked files only)."""
    if req.target not in ("library", "workspace", "untracked"):
        raise HTTPException(status_code=400, detail="target must be library, workspace, or untracked")
    results = []

    def add_one(ref, target):
        fi, rp = ref["folder_identity"], ref["relative_path"]
        if target == "untracked":
            if _is_file_tracked(fi, rp):
                return False, "already in Library"
            if any(s["folder_identity"] == fi and s["relative_path"] == rp for s in _STAGED_FILES):
                return False, "already staged"
            _STAGED_FILES.append(ref)
            return True, None
        if target == "library":
            try:
                db.add_local_file_to_library(fi, rp, ref["basename"],
                                             ref["file_size"], ref["modified_at"])
            except ValueError as e:
                return False, str(e)
            return True, None
        db.add_workspace_file(fi, rp)      # workspace: file-only item, tracked or not
        return True, None

    guard = jobs.curation_write_guard() if req.target != "untracked" else contextlib.nullcontext()
    try:
        with guard:
            for path in req.paths:
                ref = _ref_for_path(path)
                if ref is None:
                    results.append({"path": path, "added": False, "reason": "not a file"})
                    continue
                added, reason = add_one(ref, req.target)
                results.append({"path": path, "basename": ref["basename"],
                                "added": added, "reason": reason})
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"results": results, "added": sum(r["added"] for r in results)}


@app.get("/api/files/staged")
def api_files_staged():
    """Import's in-memory staged files, re-validated: drop any that vanished or since
    became tracked. Rendered in the untracked list alongside catalog files."""
    live = [s for s in _STAGED_FILES
            if os.path.isfile(os.path.join(s["folder_identity"], s["relative_path"]))
            and not _is_file_tracked(s["folder_identity"], s["relative_path"])]
    _STAGED_FILES[:] = live
    return {"files": live}


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


@app.get("/api/track/{track_id}/decision")
def api_track_decision(track_id: int):
    """Latest approve/reject for a track (with its verified-parts checklist), or {}."""
    return db.latest_decision(track_id) or {}


@app.post("/api/decision")
def api_decision(d: Decision):
    global _decision_count
    if d.decision and "youtube" not in (d.checklist or []):
        raise HTTPException(status_code=400, detail="approve requires the YouTube link to be checked")
    try:
        with jobs.curation_write_guard():
            result = db.record_decision(d.track_id, d.decision, d.checklist)
            _decision_count += 1
            if AUTO_EXPORT_EVERY and _decision_count % AUTO_EXPORT_EVERY == 0:
                db.export_csv_only()       # atomic; marks flow to the git-tracked CSV
                result["auto_exported"] = True
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


def _native_pick_files():
    """Native multi-file open dialog; returns the chosen absolute paths (localhost).
    Same subprocess-tkinter approach as the folder picker."""
    code = (
        "import tkinter, tkinter.filedialog as fd, sys\n"
        "r = tkinter.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "paths = fd.askopenfilenames(filetypes=["
        "('Audio','*.mp3 *.flac *.m4a *.opus *.ogg *.aac *.wav *.wma'),('All files','*.*')])\n"
        "sys.stdout.write('\\n'.join(r.tk.splitlist(paths)))\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return []
    if out.returncode != 0:
        return []
    return [os.path.normpath(p) for p in out.stdout.splitlines() if p.strip()]


@app.post("/api/pick-files")
def api_pick_files():
    """Pop the native multi-file picker on the server machine (localhost). Returns the
    picked absolute paths; the client hands them to /api/files/add with a target."""
    return {"paths": _native_pick_files()}


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


def _cleanup_download_targets():
    folder = _safe_download_root()
    if folder is None:
        return []
    junk_exts = settings.cleanup_extensions()   # Settings-tunable junk-extension list
    targets = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or _is_reparse_point(path):
            continue
        if not (name.lower().endswith(junk_exts) or os.path.getsize(path) == 0):
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
    return {"token": token, "expires_in": settings.delete_token_ttl(),
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
