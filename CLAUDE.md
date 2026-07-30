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

Frontend is mobile responsive: primary navigation becomes a mobile drawer;
shared lists, toolbars, and tabs adapt to narrow screens. Android is a
Capacitor 8 mobile wrapper. Native launch requires persisted FastAPI LAN
`http://` or `https://` host:port, not `localhost`; browser requests stay
relative same-origin. Device backend uses `python run.py --host 0.0.0.0`;
phone and server share Wi-Fi. Trusted LAN only: cleartext HTTP and no backend
authentication. Prefer HTTPS beyond trusted LAN. **No backend address is baked
into the APK** — the user enters it, `normalizeNativeServerUrl` validates by
RFC1918 range (not a specific host) and re-validates on every read, so DHCP
changes need no rebuild; literal addresses live only in docs and test fixtures.
`android/` is generated + gitignored, re-hardened by
`scripts/harden-android.mjs` after every sync (it fails loudly on a stale
patch); local and CI release builds share one gitignored keystore pinned to the
tracked `release-cert-sha256.txt`. See `review_app/frontend/ANDROID.md`. The
Chrome extension remains compatibility-only.

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
  Exact handoff to Workspace; Review curation; **Verify labels**; **Remove**
  (deletes the Library entry + its downloaded file, never the mp3-folder file).
  Tri-state (Tachiyomi-style) label filter, `untracked` included.
- **Verify labels** (Library + Workspace bulk button) re-checks the SELECTED
  items' link health + local/download freshness as a paced background task
  (`tasks.py`, one yt-dlp health at a time, randomized delay, cancellable,
  network-fail cutoff — 7k+ links would rate-limit). No selection → falls back to
  the scope chooser (**all** / **only unverified**, `VerifyScopeDialog.vue`). The
  task refreshes the catalog first (local labels) and writes `tracks.yt_health` /
  workspace `metadata_json`. Per-row verify lives on the labels too:
  `POST /api/{kind}/{id}/verify-{link,local,download}` (YouTube / Local file /
  Downloaded menus). **A dead/private link on an approved track sends it back to
  unreviewed** — enforced centrally in `db.set_track_health` /
  `db.unreview_track_if_dead`, so every verify path (bulk, per-row, library or
  workspace-linked) obeys it. Workspace's on-load enrich
  (`/api/workspace/enrich`) stays a separate capped foreground loop.
- **Activity** shows the log: **Background tasks** (running with progress +
  cancel, finished with result; persisted in `background_tasks`, orphaned-running
  → `interrupted` on restart) and **Decision history** (the append-only
  `decisions` log, read-only). Task rows carry per-outcome tallies
  (`ok`/`failed`/`skipped` alongside `done`/`total`/`found`); the worker bumps
  exactly one per item (success / raised / NetworkDown) and the finish message
  reads `N noun · O ok, F failed, S skipped`. `VerifyScopeDialog.vue` is the
  shared scope chooser.
- **Labels** (`labels.js` + `LabelRow.vue`, shared by Workspace, Library, and
  Import) are clickable icon badges and the row's ONLY action hub (no 3-dots):
  YouTube, Local file, Downloaded, Untracked, Confirmed, Rejected, plus
  membership **In Library** / **In Workspace** (shown wherever true via
  cross-lookup; self-referential rows hidden). Menus: YouTube (open/copy/copy-id/
  play, **Download audio…**, **Verify link**, + Find/Pick local file when local
  missing/stale);
  Local file + Downloaded
  (play/reveal/info + **Verify file/download** + **Embed metadata** +
  **Romanize filename** + **Delete**;
  Local also Find
  on YouTube / Set YouTube link…); Untracked is exactly Add to Library + Send to Workspace (Send
  hidden in Workspace) — identical on every screen via `untrackedMenuItems`;
  **In Workspace** membership label (Info, Save to library, **Find lyrics**,
  **View lyrics**, **Find metadata**, show, remove). Confirmed/Rejected menu:
  **Decision info** (read-only checklist from `GET /api/track/{id}/decision` →
  `decisionInfo`), Set unreviewed, Re-review. Review renders the same shared
  `LabelRow` for the track under review (all applicable labels + menus).
- **Embed metadata** writes our best-known artist/title into the file's tags
  (mutagen easy mode — supported tags only). `source` `local`|`download` picks
  which file. `POST /api/{track,workspace}/{id}/embed`.
- **Find lyrics / Find metadata** (In-Workspace label + Workspace bulk toolbar,
  paced `tasks.py` sweeps like Find link). Lyrics reuse the repo-root
  `lyrics_fetch` providers (LRCLIB + NetEase/Kugou/J-Lyric); stored in the item's
  `metadata_json` and, when it has a local file, as a `.lrc`/`.txt` sidecar (read
  sidecar-first). Metadata is a small MusicBrainz recording lookup (`_mb_best`)
  that auto-applies the best artist/title above `MB_MIN_SCORE`/`MB_SEARCH_LIMIT`
  candidates — reversible via Info. Generic over kind (`track`|`workspace`, like
  `/embed`): `GET/POST /api/{kind}/{id}/lyrics`, `POST /api/{kind}/{id}/lyrics/save`
  (edit), `POST /api/{kind}/{id}/find-metadata`, `GET /api/{kind}/{id}/file-tags`,
  `POST /api/tasks/find-{lyrics,metadata}/workspace` (bulk). One implementation in
  `_entity_*` helpers, thin route wrappers. See usb-ldac
  (`web/api/{lyrics,metadata}.py`) for the reference.
- **Romanize** (CJK → Hepburn, non-ASCII only so ASCII/LRC-timestamps/`[ids]`/
  extensions pass through) via `pykakasi` in `backend/romanize.py` (ported from
  usb-ldac). Lives on three surfaces: the **lyrics editor** (`LyricsView`) and
  **metadata editor** (`InfoEditDialog`) get a Romanize button that rewrites the
  draft/fields in place (user then Saves) — both call `POST /api/romanize {texts}`;
  the **Local file + Downloaded labels** get **Romanize filename**, which renames
  the file on disk (`POST /api/romanize/filename`, same `RevealRef` locator as
  reveal) and repoints DB refs via `db.rename_file_link` (track link + workspace
  ref + `tracks.filename`) + lyric sidecars, then rebuilds the catalog. Download
  files rename only (located by ASCII `[id]`, preserved). Japanese-accurate;
  Chinese falls back to Japanese on-yomi (a pykakasi limit, same as the reference).
- **Review** right panel is **Embed | Lyrics** tabs (`LyricsView.vue` +
  `lyrics.js`). Timestamped (LRC) lyrics highlight + auto-scroll to whichever
  audio is playing — the local file or the YouTube candidate, only one at a time.
  Every lyric viewer (`LyricsView`, so the Lyrics tab + `LyricsDialog`) has an
  inline **Edit** (pencil → raw LRC textarea → Save). Review always shows the
  workspace label (lyrics/metadata hub) even when the track isn't staged — its
  actions then target the track, plus **Send to workspace**. Info + edit fields
  live in the label menus (no separate top button). The "Your file" card also
  shows duration/format + the file's own embedded tags (`/file-tags`).
  An **approval checklist modal** (opens on Approve/Reject) records which parts
  the reviewer verified-correct (YouTube / Local file / Lyrics / Metadata); each
  box enables only when that part exists (yt_id / local file / lyrics found /
  MusicBrainz match) and the modal's confirm does the write. Approve is
  gated on **YouTube checked** (reject needs nothing); the checked parts are
  stored in `decisions.checklist` (JSON) and shown as chips in Activity's
  Decision history. `POST /api/decision` takes `checklist: [str]` and 400s an
  approve missing `youtube`.
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
  `MB_MIN_SCORE`, `MB_SEARCH_LIMIT`, `SEARCH_RESULT_LIMIT`) and **Advanced**
  (`DELETE_TOKEN_TTL`,
  `CLEANUP_EXTENSIONS`).
- A **download** (file in the download folder — matched by `[<id>]` in name, or
  a workspace item whose direct file ref lives in that folder) is distinct from a
  **local file** (mp3-folder catalog entry). A direct file ref is **untracked**
  only when it has no `track_id`. Never conflate.
- **Download audio** runs the repo `downloader.py` subprocess (one global
  `workspace_download` reservation, tracked as a `workspace_run`). Every download
  button asks **format** first (`FormatDialog`: opus/mp3/m4a, `AUDIO_FORMAT` env).
  Two paths, one core `_start_download_run(items, fmt, replace)`: Workspace **bulk**
  (`/api/workspace/runs/download`, item-ids, skip-existing) and the shared
  **YouTube-label** single (`POST /api/download/run {yt_ids, format, replace}`,
  works off any yt_id on Workspace/Library/Review). Single sets `replace=True` →
  downloader `YT_FORCE_REDOWNLOAD` re-downloads even a logged id and yt-dlp writes
  `.part` first, so a failed download keeps the old file; on success the app drops
  the stale old-format file (`_remove_stale_after_replace`, per-id pre/post
  file-set diff). Frontend `useAudioDownload` (format modal + run poll +
  dismissable `DownloadRunAlert`) is shared by all three screens.
- **Delete** lives on the Downloaded label (simple confirm, app output,
  `/api/download/delete`) and the Local file label + Workspace bulk. Two
  mp3-folder-delete flows, both preview → token → typed `DELETE` → revalidate →
  audit, differing only in the gate: **Library** is approved-only, by track id
  (`/api/library/delete`, backend 409s non-`check==1`/non-unique). **Workspace**
  is by item id (`/api/workspace/local-delete`), resolves each item's mp3-folder
  file server-side (direct ref or linked track) and is **not** approval-gated —
  the user curates in the Workspace — but still skips download-folder and
  out-of-folder files (only configured-folder files are deletable). Both share
  `useLocalDelete` (its `_kind` picks the endpoint); Workspace's bulk button
  targets every selected item that carries a local file.
- Folder containment and exact folder-plus-relative-path identity protect local
  operations. mp3-folder deletion always requires preview, short-lived
  token/manifest, typed `DELETE`, revalidation, and audit, and only ever touches
  configured-folder files. Library's delete is additionally approved-only
  (`check==1`); Workspace's bulk delete-local is not (the user curates there) but
  is otherwise identical. Download-file deletion is the app's own output — simple
  confirm, no token.
- `explorer /select` reveal + native tkinter folder picker are localhost-only
  (server host = user machine).
- Browser serve/open uses hostname (`localhost`), never a bare IP: YouTube's
  embedded player rejects an IP-origin referer ("Video unavailable"). `run.py`
  defaults `--host` to `localhost`. Android uses the FastAPI server LAN
  host:port; cleartext HTTP and missing backend authentication require trusted
  LAN only. Never hardcode that address in shipped code — it is user-entered
  and range-validated, and an invalid stored value yields no base URL so API
  calls throw instead of hitting the WebView's own origin.
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
