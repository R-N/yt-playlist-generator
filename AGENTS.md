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

## Workspace-first app

Primary navigation is exactly: **Import, Workspace, Library, Review, Settings**.
There are no primary Pipeline, Playlist, or Discord screens. The former Local
Files and Untracked screens are folded into Library.

- **Import** owns pasted YouTube, Discord, and an **Untracked files** tab
  (configured-folder files with no Library entry): preview, Add to Library,
  Send to Workspace.
- **Workspace** persists items, exact selection/order, 50-item batches,
  exports, and isolated download runs. A Workspace item is its own entity
  (workspace schema v5): it references a YouTube id, a library `track_id`
  (nullable FK), OR a local file directly (`folder_identity` + `relative_path`).
  At least one identity is required (table CHECK). File-only and link-less items
  are excluded from YouTube-only operations (playlist/export/download/enrich).
- **Library** merges tracks, Saved Links, and untracked local files into one
  filterable list (tri-state, Tachiyomi-style label filter). Exact handoff to
  Workspace; **Verify links** health-checks YouTube ids into `tracks.yt_health`;
  **Remove** deletes the Library entry and its downloaded file (never the
  mp3-folder file). Saved Links reach Review only after exact local-file match.
- **Review** curates exact matches; curation stays SQLite-backed.
- **Settings** owns validated mp3-folder config/rescan, the separate **download
  folder**, credentials, and failed-download cleanup.
- **Labels** (`labels.js` + `LabelRow.vue`) are the shared clickable icon
  badges for Workspace and Library. A **download** (download-folder file, keyed
  by `[<id>]` in name) is distinct from a **local file** (mp3-folder catalog).
- Folder operations validate configured directories and containment. File
  identity uses configured folder plus relative path, never basename alone.
- Selected mp3-folder deletion previews exact targets, uses short-lived
  token/manifest checks, requires typed `DELETE`, revalidates
  identity/containment, audits outcomes, and permits deletion only for approved
  files. Download-file deletion is the app's own output (simple confirm, no
  token). `explorer /select` reveal and the tkinter folder picker are
  localhost-only (server host = user machine).
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
