# AGENTS.md

Guidance for AI coding agents working in this repository. (Claude Code reads `CLAUDE.md`, which mirrors this file.)

## Overview

What started as a single tool that turns a list of YouTube URLs into playlist URLs has grown into a personal toolkit for managing a music library: extracting video IDs from a chat/forum export, downloading audio, reverse-matching local MP3s back to their YouTube source, and cleaning up the results. There is no package, build step, or test suite — each file is a standalone script run directly with `python <script>.py`.

Most scripts hardcode their config as module-level constants at the top of the file (input/output filenames, the `MP3_FOLDERS` list, boolean mode flags). To change behavior you edit those constants, not CLI args. `url_extractor.py` is the exception that uses `sys.argv`.

## Running

```bash
python playlist_generator.py     # urls.txt -> playlists.txt (the original tool)
python url_extractor.py          # dump.csv -> ids1.txt, urls.txt, playlists.txt
python downloader.py             # ids.txt -> downloads/ (audio via yt-dlp)
python searcher.py               # scans MP3_FOLDERS -> matches.csv
python cleanup_downloads.py [ext...]   # delete failed/partial/zero-byte downloads
python check_untracked.py        # matches.csv -> untracked.txt (unverified files)
python cleanup_tracked.py        # delete source MP3s already verified in matches.csv
```

Dependencies (no `requirements.txt`): `yt-dlp`, `pandas`, `mutagen`, `rapidfuzz`, `pykakasi`. `ffmpeg` must be on PATH (yt-dlp post-processing). `acoustid` is imported lazily only if `ACOUSTID_API_KEY` is set.

## Pipeline / data flow

The scripts share a set of plain-text and CSV files as their interface — there is no in-process orchestration. Two largely independent flows:

**Forward (build playlists / download):**
`dump.csv` → `url_extractor.py` → `ids*.txt` + `urls.txt` + `playlists.txt`. Separately `downloader.py` reads `ids.txt` and writes audio to `downloads/`, appending each completed ID to `downloaded_ids.txt` and failures to `error_ids.txt`.

**Reverse (match an existing local library):**
`searcher.py` walks the folders in `MP3_FOLDERS`, and for each `.mp3` searches YouTube for the originating video, ranks candidates, and appends rows to `matches.csv`. Downstream, `check_untracked.py` and `cleanup_tracked.py` read `matches.csv` to decide what is still unverified vs. safe to delete.

`playlist_generator.py` and `url_extractor.py` both emit `playlists.txt` by chunking IDs into groups of 50 and building `https://www.youtube.com/watch_videos?video_ids=...` URLs — that 50-ID chunking is the core trick of the original project.

## Data files (curation vs derived vs input)

The `check` column is hand-curated and **irreplaceable** — songs can be re-downloaded, the marks cannot. Know which file holds them:

- **`matches.xlsx` — the source of truth for curation.** This is the file edited by hand in Excel; it has the newest and most `check` marks. The review app (`review_app/`) imports from here. As of last check: 7874 rows, 6471 approved, 22 cols.
- **`matches.csv` — a derived CSV export that *lags* the xlsx.** Same schema, but written by an earlier notebook run (7876 rows, 6423 approved — 48 fewer, ~13h older). Treat as derived/stale until re-exported, **not** as the authoritative marks. Most scripts read this; the review app and any "where are my marks" question should prefer the xlsx.
- **`dump.csv` — input.** The chat/forum export (`Author`, `Content`, … 6 cols, ~5330 rows) that `url_extractor.py` filters by author to harvest video IDs. Not curation.
- **`matches - Copy*.csv` / `matches - Copy*.xlsx` — manual dated backups.** Ad-hoc snapshots of the curation at various points (2025-05-25 → 05-27). Copies where `rows == check` are approved-only exports; others are full sets with marks. Frozen history — don't edit; `review_app/backups/` (auto-snapshots on export) supersedes this habit.
- **Plain-text I/O (one value per line):** `ids.txt` (downloader input), `ids1.txt`/`ids2.txt` (extractor / notebook outputs), `urls.txt`, `playlists.txt`, `downloaded_ids.txt` (+`.bak`) / `error_ids.txt` (download logs), `untracked.txt` (unverified list), `sign_in.txt` (age-gated URLs), `cookies.txt` (exported browser cookies).

When the CSV and XLSX disagree, the **XLSX wins** (it is what the human last touched). Re-exporting from the review app rewrites both from SQLite and ends the split-brain.

**AcoustID/MusicBrainz cross-check** (`acoustid_enrich.py`): fingerprints local audio and writes `mb_artist`, `mb_title`, `mb_recording_id`, `ac_score`, `mb_confidence` (strong/weak/none), `mb_suggest` into matches. These are non-core columns, so they ride along in the review app via `extra_json` and surface in the UI's MusicBrainz panel. The confidence logic (`match_confidence`) is pure and unit-tested; the fingerprinting needs `fpcalc` + `pyacoustid` + `ACOUSTID_API_KEY`. Run enrich on `matches.csv`, then re-seed the review DB (delete `review_app/backend/review.db`) so the new columns import.

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
- `cleanup_csv.py` and `remove_index.py` are one-off CSV-repair utilities with stale hardcoded filenames (e.g. `mp3_youtube_matches_*.csv`); treat them as references for fixing malformed `matches.csv`, not as part of the regular flow.
- `check_untracked.py`, `cleanup_tracked.py`, and `searcher.py` call `is_valid_youtube_id()` in `get_metadata()` but only `searcher.py` defines it — the other two will raise `NameError` if a file's metadata can't be read and that branch is hit. Define/import it before relying on those scripts.
