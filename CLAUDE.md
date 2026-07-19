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
- Every list has an **Add files** button: OS picker (multi-select), keeps the
  absolute path, never copies (files may live outside configured folders,
  referenced in place). One entry point `/api/files/add` with `target`
  `library` (find-or-create track) / `workspace` (file-only item) / `untracked`
  (Import's in-memory `_STAGED_FILES`, survives refresh not reboot). Picker is
  localhost-only (`/api/pick-files`). Staged out-of-folder files are first-class
  (play/reveal/add) via `_untracked_ref`.
- Workspace item is its own entity (schema v5): carries a YouTube id, a library
  `track_id` (nullable FK), OR a direct file ref (`folder_identity` +
  `relative_path`) — so a link-less local file stages with no Library row. At
  least one identity required (CHECK). Persists exact selection/order, 50-item
  batches, exports, isolated downloads. YouTube-only ops (playlist/export/
  download/enrich) skip file-only + link-less items. Every endpoint returning
  workspace items routes through `_decorate_workspace_items` (adds
  `is_download_file`/`downloaded` + link-less file tags) or labels flicker. A
  dismissed finished download-run alert is remembered (localStorage) so it
  doesn't resurface on reload; active runs always show.
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
  Import) are clickable icon badges and the row's ONLY action hub (no 3-dots):
  YouTube, Local file, Downloaded, Untracked, Confirmed, Rejected, plus
  membership **In Library** / **In Workspace** (shown wherever true via
  cross-lookup; self-referential rows hidden). Menus: YouTube (open/copy/copy-id/
  play, + Find/Pick local file when local missing/stale); Local file + Downloaded
  (play/reveal/info + **Embed metadata**; Local also Find on YouTube / Set
  YouTube link…); Untracked is exactly Add to Library + Send to Workspace (Send
  hidden in Workspace) — identical on every screen via `untrackedMenuItems`;
  membership labels (Info, send/save, show, remove).
- **Embed metadata** writes our best-known artist/title into the file's tags
  (mutagen easy mode — supported tags only). `source` `local`|`download` picks
  which file. `POST /api/{track,workspace}/{id}/embed`.
- **Save to library** is one endpoint (`POST /api/workspace/save-to-library`
  `{ids}`) that routes each item server-side — carries an untracked file →
  Library track (any link carried onto it); link-only → saved link — so per-row
  and the bulk button can't drift. `_item_carries_file` is the single predicate.
- **Find link** (Workspace find-youtube/find-local, Review find-another) finds a
  missing link/file from whatever the entry carries: stored metadata, linked
  track, downloaded file, or file tags, falling back to the file-name stem
  (`_item_terms`, mirrors the frontend). Auto mode = paced background task
  (`tasks.py`) applying the best `searcher.score` hit above the min-score floor,
  excluding rejected links (reuses `decisions`). Interactive mode
  (`SearchPickerDialog.vue`) shows top-N ranked candidates + editable query, user
  picks. `ForceSetDialog.vue` force-sets a pasted id/URL (verify alive) or picked
  file (allows out-of-folder), showing match score before confirm. Local
  searches/finds call `_refresh_catalog()` first so a just-downloaded file is
  seen without a manual rescan. `InfoEditDialog.vue` shows all row fields + edits
  a fixed column allow-list (`_EDITABLE_{TRACK,WS}_COLUMNS`).
- Workspace, Library, and Import-untracked render the same list via
  `CurationList.vue`; the reactive plumbing and action dispatch live once in
  `curation.js` (`useRowActions`/`usePreview`/`usePagination`/`useSelection`/
  `useLabelFilter`/`useMembershipActions`/`useSearchPicker`/`useForceSet`/
  `useFilePicker`). Each screen only supplies a row-normalizer (maps its entity
  to a common row carrying `ytUrl`, `trackId`, `setCheck`, `embed`, `revealArg`,
  `infoFor`, media srcs) and injects genuinely per-screen bits (delete, review),
  so an action fix lands on every screen at once.
- Settings owns validated mp3 folders/rescan, the **Download folder** (separate
  destination; failed-download cleanup follows it), credentials, and tunable
  constants (all live in `settings.py` getters, not hardcoded): **Link finding**
  (`YT_SEARCH_TOP_N`, `TASK_DELAY_MIN/MAX`, `YT_MIN_SCORE`, `LOCAL_MIN_SCORE`,
  `SEARCH_RESULT_LIMIT`) and **Advanced** (`DELETE_TOKEN_TTL`,
  `CLEANUP_EXTENSIONS`).
- A **download** (file in the download folder — matched by `[<id>]` in name, or
  a workspace item whose direct file ref lives in that folder) is distinct from a
  **local file** (mp3-folder catalog entry). A direct file ref is **untracked**
  only when it has no `track_id`. Never conflate.
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
