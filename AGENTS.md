# AGENTS.md

Guidance for AI coding agents working in this repository. (Claude Code reads `CLAUDE.md`, which mirrors this file.)

## Overview

What started as a single tool that turns a list of YouTube URLs into playlist URLs has grown into a personal toolkit for managing a music library: extracting video IDs from a chat/forum export, downloading audio, reverse-matching local MP3s back to their YouTube source, cross-checking those matches against the AcoustID/MusicBrainz database, and curating the results. The repo root is a set of standalone scripts run directly with `python <script>.py` (nothing to install). On top sits `review_app/` — a FastAPI + SQLite + Vue/Vuetify web app for curating matches, with its own test suite (`unittest` + Vitest).

Most scripts hardcode their config as module-level constants at the top of the file (input/output filenames, the `MP3_FOLDERS` list, boolean mode flags). To change behavior you edit those constants, not CLI args. `url_extractor.py` is the exception that uses `sys.argv`.

## Running

```bash
python playlist_generator.py     # urls.txt -> playlists.txt (the original tool)
python url_extractor.py          # dump.csv -> ids1.txt, urls.txt, playlists.txt
python discord_fetch.py <chan>   # Discord channel -> discord.json (needs DISCORD_BOT_TOKEN)
python discord_extractor.py [f]  # discord.json/.csv -> ids1.txt, urls.txt, playlists.txt
python downloader.py             # ids.txt -> downloads/ (audio via yt-dlp)
python searcher.py               # scans MP3_FOLDERS -> matches.csv
python filter_local_quality.py   # flag tracks whose local mp3 >= 192 kbps -> ids2.txt
python acoustid_enrich.py        # AcoustID/MusicBrainz cross-check -> mb_* columns
python mb_enrich.py              # text-search MusicBrainz fallback -> fills blank mb_* rows
python lyrics_fetch.py           # LRCLIB lyrics -> .lrc/.txt sidecars in MP3_FOLDERS
python cleanup_downloads.py [ext...]   # delete failed/partial/zero-byte downloads
python check_untracked.py        # matches.csv -> untracked.txt (unverified files)
python cleanup_tracked.py        # delete source MP3s already verified in matches.csv
```

Review app (curation UI) lives in `review_app/`:
```bash
cd review_app
python install.py    # one-time: pip + npm deps   (or install.bat / .ps1 / .sh)
python run.py        # build frontend + serve SPA+API on :8000   (run.py --dev = hot reload)
```

Dependencies for the root scripts are in the root `requirements.txt` (`pip install -r requirements.txt`): `yt-dlp`, `pandas`, `mutagen`, `rapidfuzz`, `pykakasi`, `openpyxl`. `ffmpeg` must be on PATH (yt-dlp post-processing). `mb_enrich.py` and `lyrics_fetch.py` add no deps (stdlib `urllib` to MusicBrainz / LRCLIB). `acoustid` + `fpcalc` + `ACOUSTID_API_KEY` are needed only for `acoustid_enrich.py` (imported lazily). The review app has its own `review_app/backend/requirements.txt` (`fastapi`, `uvicorn`, `pandas`, `openpyxl`; `httpx` for tests).

## Pipeline / data flow

The scripts share a set of plain-text and CSV files as their interface — there is no in-process orchestration. Two largely independent flows:

**Forward (build playlists / download):**
`dump.csv` → `url_extractor.py` → `ids*.txt` + `urls.txt` + `playlists.txt`. Separately `downloader.py` reads `ids.txt` and writes audio to `downloads/`, appending each completed ID to `downloaded_ids.txt` and failures to `error_ids.txt`.

**Reverse (match an existing local library):**
`searcher.py` walks the folders in `MP3_FOLDERS`, and for each `.mp3` searches YouTube for the originating video, ranks candidates, and appends rows to `matches.csv`. Downstream, `check_untracked.py` and `cleanup_tracked.py` read `matches.csv` to decide what is still unverified vs. safe to delete.

`playlist_generator.py` and `url_extractor.py` both emit `playlists.txt` by chunking IDs into groups of 50 and building `https://www.youtube.com/watch_videos?video_ids=...` URLs — that 50-ID chunking is the core trick of the original project.

## Data files (curation vs derived vs input)

The `check` column is hand-curated and **irreplaceable** — songs can be re-downloaded, the marks cannot. Know which file holds them:

- **`matches.xlsx` + `matches.csv` — the curation, now reconciled and in sync.** Both hold the same baseline (7876 rows, 6475 approved, 22 core cols) and are **git-tracked**. The `check` column is the hand-curated, irreplaceable mark. Historically the xlsx was the hand-edited source of truth and the csv lagged it (a 6471 vs 6423 split-brain); that was reconciled — union of rows + xlsx-priority marks, never dropping a decision — and promoted to both files. Going forward the review app's SQLite is the live store and its **Export** rewrites both atomically.
- **`dump.csv` — input.** The chat/forum export (`Author`, `Content`, … 6 cols, ~5330 rows) that `url_extractor.py` filters by author to harvest video IDs. Not curation.
- **`matches - Copy*.csv` / `matches - Copy*.xlsx` — manual dated backups.** Ad-hoc snapshots of the curation at various points (2025-05-25 → 05-27). Copies where `rows == check` are approved-only exports; others are full sets with marks. Frozen history — don't edit; `review_app/backups/` (auto-snapshots on export) supersedes this habit.
- **Plain-text I/O (one value per line):** `ids.txt` (downloader input), `ids1.txt`/`ids2.txt` (extractor / notebook outputs), `urls.txt`, `playlists.txt`, `downloaded_ids.txt` (+`.bak`) / `error_ids.txt` (download logs), `untracked.txt` (unverified list), `sign_in.txt` (age-gated URLs), `cookies.txt` (exported browser cookies).

When the CSV and XLSX disagree, the **XLSX wins** (it is what the human last touched). Re-exporting from the review app rewrites both from SQLite and ends the split-brain.

**AcoustID/MusicBrainz cross-check** (`acoustid_enrich.py`): fingerprints local audio and writes `mb_artist`, `mb_title`, `mb_recording_id`, `ac_score`, `mb_confidence` (strong/weak/none), `mb_suggest` into matches. These are non-core columns, so they ride along in the review app via `extra_json` and surface in the UI's MusicBrainz panel. The confidence logic (`match_confidence`) is pure and unit-tested; the fingerprinting needs `fpcalc` + `pyacoustid` + `ACOUSTID_API_KEY`. Run enrich on `matches.csv`, then re-seed the review DB (delete `review_app/backend/review.db`) so the new columns import.

## Review app (`review_app/`)

FastAPI + SQLite backend (`backend/`) and a Vue 3 + Vuetify / Vite frontend (`frontend/`) for curating matches by ear instead of hand-editing the spreadsheet. Play the local mp3 next to the YouTube candidate (embedded IFrame, plus an audio-only preview so you can verify by ear even when the embed is blocked — served by the read-only `GET /api/yt_audio/{yt_id}`, which resolves the candidate's audio via `yt-dlp -g` and 302-redirects the `<audio>` element to it) and the MusicBrainz cross-check, then approve/reject with one key (`A`/`→`, `R`/`←`, `↑` back).

- **SQLite is the live store** (`backend/review.db`, created on first run). It reconciles `matches.csv` + `matches.xlsx` into a `tracks` table; non-core columns are preserved in an `extra_json` blob (flattened back out by `db._expand_extra` so the API sees `mb_*` etc.). Re-seed by deleting `review.db`. CSV/XLSX are import/export/backup formats, not the live store.
- **Curation safety** (the marks are irreplaceable): the `decisions` table is **append-only**, each approve/reject is one atomic SQLite transaction, **Export** snapshots the old files into `backups/` then writes via temp-file + rename, and marks auto-export to `matches.csv` every 25 decisions (`AUTO_EXPORT_EVERY`). The app never writes or deletes audio — the audio endpoint is read-only and Range-served (so the player can scrub).
- **Run:** `python install.py` then `python run.py` (built mode = build frontend, serve SPA+API on :8000) or `python run.py --dev` (uvicorn `--reload` + Vite on :5173). Native `.bat`/`.ps1`/`.sh` wrappers forward args.
- **Config** is in `backend/config.py` (paths, `MP3_FOLDERS`, `MATCHES_SOURCE`, `AUTO_EXPORT_EVERY`).
- **Toolkit integration (tabbed UI).** Beyond Review, the app orchestrates the root scripts so they don't need a shell. `backend/settings.py` reads/writes a gitignored `.env` at the repo root (`DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `ACOUSTID_API_KEY`) and applies it to `os.environ` on startup (`setdefault`, so real env wins); secrets are masked in `/api/settings`. `backend/jobs.py` runs each root script as a **subprocess** job (not import — preserves their module-level config + file-interface contract) with captured log / status / stop; `cleanup_downloads` + `cleanup_tracked` are marked `destructive` and the Pipeline tab makes them require a typed `DELETE`. `backend/discord_service.py` reuses the repo-root `discord_fetch.py` + `discord_extractor.py` (added to `sys.path`) to fetch a channel and write `ids.txt`/`urls.txt`/`playlists.txt`. `GET /api/likes/queue` serves `ids.txt` to the `extension/` Chrome liker. The frontend is split into `App.vue` (tab shell) + `ReviewTab/DiscordTab/PipelineTab/SettingsTab.vue`.
- **Chrome extension** (`extension/`, repo root): likes harvested video ids on the user's account from their logged-in session via YouTube's internal `youtubei/v1/like/like` (SAPISIDHASH from the `SAPISID` cookie). MV3, throttled, Stop button. No OAuth. Brittle to YouTube internals; bulk liking can trip spam limits.
- **Tests** (run from `review_app/backend`): `python -m unittest discover -p "test_*.py"` (`test_db.py` data layer, `test_api.py` endpoints via FastAPI TestClient — needs `httpx`, `test_integrations.py` settings/.env + Discord service + job catalog with network stubbed); frontend `cd frontend && npm run test` (Vitest over the pure helpers in `src/review.js`). Root scripts also have tests: `test_filter.py`, `test_acoustid.py`, `test_discord.py` (extractor regex, run from repo root), and `review_app/test_scripts.py` for the launchers.

## Key cross-cutting conventions

- **Resume via append-only logs.** Long-running scripts treat their output as a checkpoint: `downloader.py` skips IDs already in `downloaded_ids.txt`; `searcher.py` skips files already passing in `matches.csv`. Re-running is safe and resumes where it left off — deleting the log forces a full redo.
- **YouTube ID = 11 chars** matching `[a-zA-Z0-9_-]{11}`, frequently embedded in filenames as `[<id>]` (the yt-dlp `outtmpl` in `downloader.py` writes them this way; `searcher.py`/`cleanup_downloads.py` parse them back out). The ID is also stored in each file's `composer`/`yt_id` metadata tag.
- **Age-restricted / private handling.** When yt-dlp hits "sign in to confirm your age", the URL is appended to `sign_in.txt` and the call is retried with `cookiefile: "cookies.txt"`. With the `sign_in_only` flag set, a run processes *only* the URLs listed in `sign_in.txt` — used for a second cookie-authenticated pass over what failed the first time. `cookies.txt` is a real exported-cookies file in the repo.
- **Mode flags in `searcher.py`.** The booleans near the top (`no_search`, `sign_in_only`, `no_mhtml`, `fix_mhtml`, `no_id`) reconfigure `main()` for different passes over the same library — e.g. `fix_mhtml` re-processes only rows whose chosen format came back as `mhtml` (a failed/storyboard result), `no_search` trusts existing IDs without querying.

## `searcher.py` matching heuristic

The interesting logic lives in `score()`. It picks the best YouTube result for a local track using a hand-tuned additive scoring system, not exact match:

- Rewards official-looking channels (`- topic`, `release`, `official`, `vevo`, channel IDs starting with `UC`/`@`) and fuzzy similarity between the file's artist/title and the candidate's channel/title.
- Penalizes unwanted versions (`instrumental`, `live`, `remix`, `nightcore`, `karaoke`, `tv size`, `8bit`, etc.) — but only when that term is absent from the query, so an intentionally-sought "live" track isn't penalized.
- Japanese text is romanized via `pykakasi` before comparison so Latin queries can match Japanese channel/title strings; `remove_symbols`/`clean_artist`/`clean_query` strip decorative characters and download-site noise.
- Prefers higher-bitrate, better-codec audio formats (`opus > ogg > aac > m4a > mp3`).

When tuning match quality, adjust the reward/penalty dicts and the fuzzy thresholds in `score()` rather than adding new control flow.

## Notes

- Windows-first: paths in `MP3_FOLDERS` are absolute `E:/...` drive paths; update them for any other machine.
- The repo working tree is messy by design — many `matches - Copy*.csv/.xlsx` snapshots, `tmp*.tmp`, and `*.bak` files are manual backups, not generated artifacts. Don't assume they're safe to delete without asking.
- `.gitignore` deliberately **tracks** `matches.csv` and `matches.xlsx` (the curation) while ignoring everything else regenerable: the `matches - Copy*` backups, `review_app/backups/`, `review_app/backend/review.db*`, `review_app/frontend/node_modules` + `dist`, `__pycache__`, and the plain-text I/O files. Commit `matches.csv`/`.xlsx` to version the marks.
- `cleanup_csv.py` and `remove_index.py` are one-off CSV-repair utilities with stale hardcoded filenames (e.g. `mp3_youtube_matches_*.csv`); treat them as references for fixing malformed `matches.csv`, not as part of the regular flow.
- `is_valid_youtube_id()` is now defined locally in `check_untracked.py` and `cleanup_tracked.py` (previously only `searcher.py` had it, so their `get_metadata()` would `NameError`). Both also gained an `if __name__ == "__main__"` guard — important for `cleanup_tracked.py`, which deletes source MP3s and previously ran on import.
