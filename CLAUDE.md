# CLAUDE.md

Guidance for Claude Code working in this repository.

Root scripts are standalone compatibility tools. `review_app/` is primary
Workspace-first application: FastAPI + SQLite backend, Vue/Vuetify frontend.

## Run

```bash
python playlist_generator.py
python url_extractor.py
python discord_fetch.py <chan>
python discord_extractor.py [f]
python downloader.py
python searcher.py
python filter_local_quality.py
python acoustid_enrich.py
python mb_enrich.py
python lyrics_fetch.py
python tag_enrich.py
python cleanup_downloads.py [ext...]
python check_untracked.py
python cleanup_tracked.py
```

```bash
cd review_app
python install.py
python run.py
python run.py --dev
```

## Current app contract

Primary nav: **Import, Workspace, Library, Review, Activity, Settings**.
Pipeline, Playlist, and Discord are not primary screens. Local Files + Untracked
were merged into Library.

- Import owns pasted YouTube, Discord, and an **Untracked files** tab (files in
  configured folders with no Library entry): preview, Add to Library, Send to
  Workspace.
- Workspace item is its own entity (schema v5): carries a YouTube id, a library
  `track_id` (nullable FK), OR a direct file ref (`folder_identity` +
  `relative_path`) — so a link-less local file stages with no Library row. At
  least one identity required (CHECK). Persists exact selection/order, 50-item
  batches, exports, isolated downloads. YouTube-only ops (playlist/export/
  download/enrich) skip file-only + link-less items. Row 3-dots menu is exactly
  Save to Library / Show in library / Remove; Show in library is hidden for
  file-only items (no `track_id`). A dismissed finished download-run alert is
  remembered (localStorage) so it doesn't resurface on reload; active runs
  always show.
- Library merges tracks, Saved Links, and untracked local files in one list.
  Exact handoff to Workspace; Review curation; **Verify links**; **Remove**
  (deletes the Library entry + its downloaded file, never the mp3-folder file).
  Tri-state (Tachiyomi-style) label filter, `untracked` included.
- **Verify links** (Library + Workspace) is a paced background task, not a
  blocking loop: 7k+ links would rate-limit. The button asks scope — **all** or
  **only unverified** (fewer = faster) — then starts one worker thread
  (`tasks.py`) that resolves yt-dlp health with a randomized delay, one verify
  at a time (global rate limit), cancellable, with a network-fail cutoff. Writes
  `tracks.yt_health` / workspace `metadata_json`. Workspace's on-load enrich
  (`/api/workspace/enrich`) stays a separate capped foreground loop.
- **Activity** shows the log: **Background tasks** (running with progress +
  cancel, finished with result; persisted in `background_tasks`, orphaned-running
  → `interrupted` on restart) and **Decision history** (the append-only
  `decisions` log, read-only). `VerifyScopeDialog.vue` is the shared scope chooser.
- **Labels** (`labels.js` + `LabelRow.vue`, shared by Workspace, Library, and
  Import) are clickable icon badges: YouTube, Local file, Downloaded, Untracked,
  Confirmed, Rejected. Each opens a context menu (open/copy/play/reveal/delete).
- Workspace, Library, and Import-untracked render the same list via
  `CurationList.vue`; the reactive plumbing and action dispatch live once in
  `curation.js` (`useRowActions`/`usePreview`/`usePagination`/`useSelection`/
  `useLabelFilter`). Each screen only supplies a row-normalizer (maps its entity
  to a common row carrying `ytUrl`, `trackId`, `setCheck`, `revealArg`,
  `infoFor`, media srcs) and injects genuinely per-screen bits (delete, review),
  so an action fix lands on every screen at once.
- Settings owns validated mp3 folders/rescan, the **Download folder** (separate
  destination; failed-download cleanup follows it), and credentials.
- A **download** (file in the download folder, matched by `[<id>]` in name) is
  distinct from a **local file** (mp3-folder catalog entry). Never conflate.
- Folder containment and exact folder-plus-relative-path identity protect local
  operations. Selected mp3-folder deletion is approved-only and requires
  preview, short-lived token/manifest, typed `DELETE`, revalidation, and audit.
  Download-file deletion is the app's own output — simple confirm, no token.
- `explorer /select` reveal + native tkinter folder picker are localhost-only
  (server host = user machine).
- Serve/open the app by hostname (`localhost`), never a bare IP: YouTube's
  embedded player rejects an IP-origin referer ("Video unavailable"). `run.py`
  defaults `--host` to `localhost`; keep it a hostname.
- Root scripts/plain files remain standalone compatibility, not app primary
  contract. Extension queue endpoint is compatibility only.

Preserve curation `check` marks and append-only decisions (unreview clears the
mark, keeps history). SQLite is live state; CSV/XLSX are import/export formats;
`yt_health` is a runtime cache, excluded from export.

## Tests

```bash
cd review_app/backend
python -m unittest discover -p "test_*.py"
cd ../frontend
npm run test
npm run build
```

Run root `test_*.py` tests from repository root when changing root scripts.
