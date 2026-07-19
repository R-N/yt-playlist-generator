# Workspace-first app

FastAPI + SQLite backend and Vue 3/Vuetify frontend for managing YouTube
items, downloads, local files, and curation.

## Navigation

Primary navigation is **Import, Workspace, Library, Review, Activity, Settings**.
Pipeline, Playlist, and Discord are not primary screens. The old Local Files
and Untracked screens are merged into Library.

- **Import** owns pasted YouTube, Discord, and an **Untracked files** tab
  (configured-folder files with no Library entry): preview, Add to Library,
  Send to Workspace.
- **Workspace** persists items, exact selection/order, 50-item batches,
  exports, and isolated download runs. A Workspace item is its own entity
  (schema v5): it references a YouTube id, a library track (nullable FK), OR a
  local file directly (folder identity + relative path), so link-less files can
  stage without a Library row.
- **Library** merges tracks, Saved Links, and untracked local files in one
  filterable list. Exact handoff to Workspace, Verify links (YouTube health),
  Remove (drops the Library entry + downloaded file), and Review handoff.
- **Review** curates exact local-file matches; curation remains SQLite-backed.
  Its menu offers **find another YouTube link** for a track.
- **Activity** is the log: **Background tasks** (verify + find sweeps — running
  with progress + cancel, finished with a result; persisted in
  `background_tasks`, running rows marked `interrupted` on restart) and
  **Decision history** (the append-only `decisions` log, read-only).
- **Settings** validates mp3 folders, sets the separate download folder,
  rescans the catalog, stores credentials, runs failed-download cleanup, and
  tunes the **Link finding** and **Advanced** knobs (below).

Every list has an **Add files** button that adds absolute-path files via the OS
picker (multi-select, no copy — files may live outside configured folders and
are referenced in place): Import stages them as in-memory untracked files
(survives refresh, not reboot), Library adds them as tracks, Workspace adds
file-only items. `/api/files/add` is the one entry point (`target`
`library`/`workspace`/`untracked`); `/api/pick-files` is the localhost picker.

**Verify links** (Library + Workspace) is a paced background task, not a blocking
loop — verifying thousands of links back-to-back would rate-limit. The button
asks scope (**all** vs **only unverified**), then one worker thread (`tasks.py`)
resolves yt-dlp health with a randomized delay, one verify at a time (a second
request gets HTTP 409), cancellable, with a network-failure cutoff. Track it in
Activity (`GET /api/tasks`); scope chooser is `VerifyScopeDialog.vue`.

**Labels** are shared clickable icon badges (`labels.js` / `LabelRow.vue`) used
by Workspace, Library, and Import: YouTube, Local file, Downloaded, Untracked,
Confirmed, Rejected, plus the membership labels **In Library** and **In
Workspace** (shown wherever true via cross-lookup; self-referential rows
hidden). Each label click opens a context menu — the label is the row's action
hub (there is no 3-dots menu). A **download** (file in the download folder) is
distinct from a **local file** (mp3-folder catalog entry); a direct file ref is
**untracked** only when it has no Library track. Notable menu actions:

- YouTube: open/copy/copy-id/play; **Find local file** and **Pick local file…**
  when the local file is missing/stale.
- Local file / Downloaded: play/reveal/info and **Embed metadata** — write our
  best-known artist/title into the file's tags (mutagen easy mode, supported
  tags only). Local file also has **Find on YouTube** / **Set YouTube link…**.
- Untracked (every screen): exactly **Add to Library** + **Send to Workspace**
  (Send hidden in Workspace).
- In Library / In Workspace: Info, send/save, show, remove. **Save to library**
  routes server-side per item — a file becomes a Library track (any link carried
  onto it), a link-only item becomes a saved link — so per-row and the bulk
  button share one endpoint (`POST /api/workspace/save-to-library`).

**Find link** finds a missing YouTube link or local file from whatever the entry
already carries (stored metadata, a linked track, downloaded file, or file tags,
falling back to the file name). Two modes over the same `searcher.score` ranker:
auto (paced background task in `tasks.py` — Workspace find-youtube/find-local,
Review's find-another — applies the best hit above the score floor, excluding
rejected links) and interactive (`SearchPickerDialog.vue`: top-N ranked
candidates with scores + an editable query, user chooses). **Set YouTube link…**
/ **Pick local file…** (`ForceSetDialog.vue`) force-set a pasted id/URL or a
picked file, verifying it's alive/present and showing the match score before
confirming. Local searches refresh the catalog first so a just-downloaded file
is found without a manual rescan. Every db-backed row also has an **Info** modal
(`InfoEditDialog.vue`) showing all fields, with edit of a fixed column allow-list.

**Link finding** settings: search top-N, task delay min/max, YouTube/local min
score (accept-floor for auto-apply), and picker result count. **Advanced**
settings: delete-token TTL and the download-cleanup junk-extension list.

Workspace, Library, and Import-untracked render the same list via
`CurationList.vue`. The reactive plumbing and per-action logic live once in
`curation.js` (`useRowActions` / `usePreview` / `usePagination` /
`useSelection` / `useLabelFilter` / `useMembershipActions` / `useSearchPicker` /
`useForceSet` / `useFilePicker`); each screen supplies only a row-normalizer
(filling `embed`, media srcs, `revealArg`, `infoFor`, …) and injects genuinely
per-screen actions (delete, review), so a fix to any shared action applies
everywhere.

## Safety

- Configured folders require validation. Operations enforce containment.
- File identity is configured folder plus relative path, not basename.
- Selected mp3-folder deletion is approved-only. Preview shows exact targets;
  confirmation requires short-lived token/manifest and typed `DELETE`, then
  revalidates containment and identity and records an audit.
- Removing a Library entry drops the row + its downloaded file only; mp3-folder
  files are never touched by it. Download-file deletion is the app's own output
  (simple confirm). Reveal/`explorer /select` and the folder picker are
  localhost-only.
- Failed-download cleanup uses immutable preview manifests and safeguarded
  confirmation; it does not rescan at confirmation time.
- SQLite decisions are append-only (unreview clears the current mark, keeps
  history). Export snapshots existing CSV/XLSX before atomic replacement;
  `yt_health` is a runtime cache and is not exported.

## Setup

```bash
cd review_app
python install.py
```

Wrappers remain available:

```bash
install.bat
.\install.ps1
./install.sh
```

## Run

```bash
cd review_app
python run.py                 # build and serve on :8000
python run.py --dev           # uvicorn reload + Vite on :5173
```

Also available: `run.bat`, `run.ps1`, `run.sh`, `--port N`, `--host H`,
`--no-install`, and built-mode `--no-build`.

`--host` defaults to `localhost` (a hostname), not `127.0.0.1`. YouTube's
embedded player rejects a bare-IP origin referer and renders "Video
unavailable", so open the app at `http://localhost:PORT`.

## Optional MusicBrainz cross-check

From repository root, `acoustid_enrich.py` can fingerprint local MP3s using
`fpcalc`, `pyacoustid`, and `ACOUSTID_API_KEY`. It is resumable; export first,
then re-seed DB only when importing changed source data.

## Tests

```bash
cd review_app/backend
python -m unittest discover -p "test_*.py" -v
cd ../frontend
npm run test
npm run build
```

Backend tests isolate DB/CSV/XLSX and folders in temporary directories. Root
scripts retain their standalone tests and commands. The extension queue
endpoint (`GET /api/likes/queue`) is compatibility integration only.
