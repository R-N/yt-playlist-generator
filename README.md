# yt-playlist-generator

YouTube-sourced music library toolkit with a Workspace-first web app. Root
scripts remain standalone compatibility commands; the app is the primary way
to manage imports, saved work, local files, downloads, and curation.

## Requirements

- Python 3
- `pip install -r requirements.txt`
- [`ffmpeg`](https://ffmpeg.org/) on `PATH` for audio extraction

## App

```bash
cd review_app
python install.py
python run.py                 # built app at http://127.0.0.1:8000
python run.py --dev           # Vite hot reload at http://localhost:5173
```

Primary navigation: **Import, Workspace, Library, Review, Settings**. The former
Local Files and Untracked screens are folded into Library.

- **Import** handles pasted YouTube links/IDs, Discord imports, and an
  **Untracked files** tab (configured-folder files with no Library entry):
  preview, Add to Library, Send to Workspace.
- **Workspace** persists items, exact selection/order, 50-item batches, exports,
  and isolated download runs. A Workspace item references a YouTube id, a
  library track, or a local file directly, so link-less files can stage there.
- **Library** merges tracks, Saved Links, and untracked local files in one
  filterable list. Exact handoff to Workspace, Verify links (YouTube health),
  and Remove (drops the entry + downloaded file). Saved Links require exact
  local-file matching before Review.
- **Review** curates exact local-file matches.
- **Settings** validates mp3 folders, sets the separate download folder,
  rescans the catalog, stores credentials, and owns failed-download cleanup.

Shared clickable **labels** (YouTube, Local file, Downloaded, Untracked,
Confirmed, Rejected) appear in Workspace and Library. A **download**
(download-folder file) is distinct from a **local file** (mp3-folder catalog).

Folder operations use configured-folder containment and exact folder-plus-
relative-path identity. Selected mp3-folder deletion is approved-only: preview
exact targets, confirm short-lived token/manifest, type `DELETE`, revalidate
identity and containment, then audit. Removing a Library entry drops only the
row + its downloaded file. Failed-download cleanup confirms against an immutable
manifest and does not rescan at confirm.

## Standalone compatibility scripts

Run directly from repository root. They retain plain-file interfaces and are
not the app's primary contract.

| Script | Does |
| --- | --- |
| `playlist_generator.py` | `urls.txt` → 50-item YouTube playlist URLs in `playlists.txt`. |
| `url_extractor.py` | Extracts IDs from `dump.csv`; writes legacy text outputs. |
| `discord_fetch.py` | Fetches a Discord channel to `discord.json`. |
| `discord_extractor.py` | Extracts YouTube IDs from Discord exports. |
| `downloader.py` | Downloads IDs from `ids.txt` to `downloads/`. |
| `searcher.py` | Matches local MP3s to YouTube and writes `matches.csv`. |
| `filter_local_quality.py` | Flags local files at least 192 kbps. |
| `acoustid_enrich.py` / `mb_enrich.py` | MusicBrainz cross-check/enrichment. |
| `lyrics_fetch.py` | Writes lyrics sidecars. |
| `tag_enrich.py` | Writes canonical audio tags when confident. |
| `cleanup_downloads.py` | Removes failed/partial downloads. |
| `check_untracked.py` | Writes legacy untracked list. |
| `cleanup_tracked.py` | Legacy verified-source cleanup. |

Root commands:

```bash
python playlist_generator.py
python url_extractor.py
python discord_fetch.py <channel_id>
python discord_extractor.py [file]
python downloader.py
python searcher.py
```

## Curation and extension

SQLite is live app state. `matches.csv` and `matches.xlsx` are import/export
formats; `check` marks and append-only decisions are preserved. The Chrome
extension's `/api/likes/queue` integration is compatibility only; it consumes
legacy `ids.txt`, not Workspace navigation.

## Tests

```bash
cd review_app/backend
python -m unittest discover -p "test_*.py" -v
cd ../frontend
npm run test
npm run build
```

Run affected root `test_*.py` files from repository root. Network and yt-dlp
flows are environment-dependent.

See [`review_app/README.md`](review_app/README.md) and
[`extension/README.md`](extension/README.md).
