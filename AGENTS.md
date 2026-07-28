# AGENTS.md

Guidance for coding agents. `CLAUDE.md` mirrors this file.

## Overview

Root scripts remain standalone compatibility tools for extracting YouTube IDs,
downloading audio, matching local files, enriching metadata, and maintaining
legacy plain-file outputs. `review_app/` is the primary Workspace-first app:
FastAPI + SQLite backend and Vue/Vuetify frontend.

## Running

Root compatibility commands:

```bash
python playlist_generator.py     # urls.txt -> playlists.txt
python url_extractor.py          # dump.csv -> ids.txt, urls.txt, playlists.txt
python discord_fetch.py <chan>   # Discord channel -> discord.json
python discord_extractor.py [f]  # Discord export -> ids1.txt, urls.txt, playlists.txt
python downloader.py              # ids.txt -> downloads/
python searcher.py                # scans MP3_FOLDERS -> matches.csv
python filter_local_quality.py   # writes ids2.txt
python acoustid_enrich.py        # writes mb_* columns
python mb_enrich.py              # MusicBrainz text-search fallback
python lyrics_fetch.py            # writes lyrics sidecars
python tag_enrich.py              # writes canonical audio tags
python cleanup_downloads.py [ext...]  # failed/partial download cleanup
python check_untracked.py         # writes untracked.txt
python cleanup_tracked.py         # legacy verified-source cleanup
```

App setup/run:

```bash
cd review_app
python install.py
python run.py                    # build frontend + serve on :8000
python run.py --dev              # hot reload; Vite on :5173
```

Retain root commands for compatibility. They are not app primary contract.

The frontend is mobile responsive: primary navigation becomes a mobile drawer,
and shared lists, toolbars, and tabs adapt to narrow screens. Android uses a
Capacitor 8 wrapper. Native launch requires a persisted FastAPI LAN base URL,
entered as an `http://` or `https://` host and port (not `localhost`); browser
requests remain relative same-origin URLs. Device backend launch is
`python run.py --host 0.0.0.0`; phone and server must share Wi-Fi. Use trusted
LAN only because cleartext HTTP is enabled and backend has no authentication;
prefer HTTPS beyond trusted LAN. See `review_app/frontend/ANDROID.md` for build
and device setup. The Android wrapper is the mobile client; the Chrome
extension remains compatibility-only.

## Workspace-first app

Primary navigation is exactly: **Import, Workspace, Library, Review, Activity,
Settings**. There are no primary Pipeline, Playlist, or Discord screens. The
former Local Files and Untracked screens are folded into Library.

- **Import** owns pasted YouTube, Discord, and an **Untracked files** tab
  (configured-folder files with no Library entry): preview, Add to Library,
  Send to Workspace.
- **Workspace** persists items, exact selection/order, 50-item batches,
  exports, and isolated download runs. A Workspace item is its own entity
  (workspace schema v5): it references a YouTube id, a library `track_id`
  (nullable FK), OR a local file directly (`folder_identity` + `relative_path`).
  At least one identity is required (table CHECK). File-only and link-less items
  are excluded from YouTube-only operations (playlist/export/download/enrich).
  The row 3-dots menu is exactly Save to Library / Show in library / Remove, with
  Show in library hidden for file-only items (no `track_id`). A dismissed
  finished download-run alert is remembered in localStorage so it stops
  resurfacing on reload; active runs always show.
- **Library** merges tracks, Saved Links, and untracked local files into one
  filterable list (tri-state, Tachiyomi-style label filter incl. `untracked`).
  Exact handoff to Workspace; **Verify labels** (see below); **Remove** deletes
  the Library entry and its downloaded file (never the mp3-folder file). Saved
  Links reach Review only after exact local-file match.
- **Verify labels** (Library + Workspace toolbars) re-checks the SELECTED items'
  link health + local/download freshness as a paced background task — verifying
  7k+ links back-to-back would rate-limit. No selection → scope chooser (**all**
  vs **only unverified**). `tasks.run` starts ONE worker thread resolving yt-dlp
  health with a randomized delay, one at a time (409 if another is active),
  cancellable, consecutive-network-fail cutoff; refreshes the catalog + runs the
  shared `_verify_entity_local` per selected item first. Persists
  `tracks.yt_health` / workspace `metadata_json`. Per-row verify lives on the
  labels: `POST /api/{kind}/{id}/verify-{link,local,download}`. **A dead/private
  link on an approved track sends it back to unreviewed** — centralized in
  `db.set_track_health` / `db.unreview_track_if_dead`, so every verify path obeys
  it. The old capped `/api/workspace/enrich` on-load loop remains.
- **Activity** is the log: **Background tasks** (`background_tasks` table; running
  → progress + cancel, finished → result; running rows become `interrupted` on
  restart) and **Decision history** (`decisions`, append-only, read-only). Scope
  chooser is `VerifyScopeDialog.vue`; the list polls `GET /api/tasks`.
- **Review** curates exact matches; curation stays SQLite-backed.
- **Settings** owns validated mp3-folder config/rescan, the separate **download
  folder**, credentials, and failed-download cleanup.
- **Labels** (`labels.js` + `LabelRow.vue`) are the shared clickable icon
  badges for Workspace, Library, and Import. A **download** (download-folder
  file, keyed by `[<id>]` in name) is distinct from a **local file** (mp3-folder
  catalog). Menu actions include **Download audio…** (format modal opus/mp3/m4a,
  `_start_download_run`; YouTube-label single = replace-on-success), per-row
  **Verify link/file/download**, **Embed metadata**, **Romanize filename** (CJK →
  Hepburn via `pykakasi`, `backend/romanize.py`; also on lyrics + metadata
  editors), and **Delete**.
- Workspace, Library, and Import-untracked share one list view
  (`CurationList.vue`); reactive plumbing and action dispatch live once in
  `curation.js` (`useRowActions`/`usePreview`/`usePagination`/`useSelection`/
  `useLabelFilter`). Each screen supplies only a row-normalizer and injects
  per-screen bits (delete, review), so an action fix lands everywhere at once.
- Folder operations validate configured directories and containment. File
  identity uses configured folder plus relative path, never basename alone.
- mp3-folder deletion previews exact targets, uses short-lived token/manifest
  checks, requires typed `DELETE`, revalidates identity/containment, audits
  outcomes, and only touches configured-folder files. **Library** delete is
  approved-only (by track id); **Workspace** bulk/label delete (by item id,
  resolves each item's file server-side) is not approval-gated — the user curates
  there — but is otherwise identical (both share `useLocalDelete`). Download-file
  deletion is the app's own output (simple confirm, no token). `explorer /select`
  reveal and the tkinter folder picker are localhost-only (server = user machine).
- Browser serve/open uses hostname (`localhost`), never a bare IP: YouTube's
  embedded player rejects an IP-origin referer ("Video unavailable"). `run.py`
  defaults `--host` to `localhost`. Android device use instead enters the
  FastAPI server's LAN host and port; use trusted LAN only because cleartext
  HTTP is enabled and backend has no authentication.
- Failed-download cleanup uses an immutable preview manifest and safeguards;
  it does not rescan a changing directory at confirmation time.
- `GET /api/likes/queue` and the Chrome extension are compatibility only; they
  consume the legacy queue, not primary app navigation.

SQLite is live curation/workspace state. `matches.csv` and `matches.xlsx` are
import/export formats; curation `check` marks and append-only decisions are
irreplaceable (unreview clears the current mark but keeps history). `yt_health`
is a runtime cache and is not exported. Root scripts still use their documented
plain-file interfaces when run directly.

## Tests

From `review_app/backend`:

```bash
python -m unittest discover -p "test_*.py"
```

From `review_app/frontend`:

```bash
npm run test
npm run build
```

From repository root, run root `test_*.py` discovery or affected tests. Network
and yt-dlp integration surfaces remain environment-dependent.

## Conventions

- YouTube IDs are 11 characters matching `[a-zA-Z0-9_-]{11}`.
- Root scripts use module-level constants and append-only checkpoints when run
  standalone; `url_extractor.py` also accepts its documented positional input.
- Windows-first paths may use absolute `E:/...` folders. Validate and configure
  folders in app Settings before local operations.
- `tag_enrich.py` writes audio tags. Cleanup operations can delete files only
  through their documented safety gates.
