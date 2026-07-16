# yt-playlist-generator

A small toolkit for managing a YouTube-sourced music library. The original tool just turns a list of video URLs into a YouTube playlist link; it grew to cover downloading audio, matching local MP3s back to their YouTube source, cross-checking those matches against the AcoustID/MusicBrainz database, and curating the results. There's also a small web app (`review_app/`) for reviewing matches by ear.

The root scripts are standalone — run each directly with `python <script>.py`, no install. Configuration lives in constants at the top of each file (input/output filenames, the `MP3_FOLDERS` list, mode flags); edit those to change behavior. The web app is the exception — it has its own setup (see [`review_app/README.md`](review_app/README.md)).

## Requirements

- Python 3
- `pip install -r requirements.txt` (yt-dlp, pandas, mutagen, rapidfuzz, pykakasi, openpyxl)
- [`ffmpeg`](https://ffmpeg.org/) on your `PATH` (used by yt-dlp for audio extraction)

Only `playlist_generator.py` needs nothing beyond Python — the rest pull in the packages above.

## Generate a playlist URL (the original tool)

1. Paste the URLs of the YouTube videos into `urls.txt`, one per line. Each URL must look like `https://www.youtube.com/watch?v=<ID>` or `https://youtu.be/<ID>`.
2. Run:
   ```bash
   python playlist_generator.py
   ```
   The playlist URL(s) are printed and written to `playlists.txt`. IDs are grouped in batches of 50 per playlist URL.

   The URL may be long; opening it in a browser makes YouTube generate a shorter playlist link.

## Other scripts

| Script | Does |
| --- | --- |
| `url_extractor.py` | Pulls YouTube video IDs out of a chat/forum export (`dump.csv`, filtered by author) and writes `ids1.txt`, `urls.txt`, and `playlists.txt`. |
| `discord_fetch.py` | Pulls a Discord channel's messages via the bot REST API (`DISCORD_BOT_TOKEN`) and writes them to `discord.json`. Usage: `python discord_fetch.py <channel_id>`. |
| `discord_extractor.py` | Reads a Discord export (`discord.json` or `.csv` from DiscordChatExporter) and extracts every YouTube video ID into `ids1.txt`/`urls.txt`/`playlists.txt`. Handles `youtu.be`, `watch?v=`, `shorts/`, `live/`, `embed/`, and embed metadata. |
| `downloader.py` | Reads video IDs from `ids.txt` and downloads each as audio (Opus) into `downloads/`, with thumbnail and metadata embedded. Tracks progress in `downloaded_ids.txt` / `error_ids.txt`. |
| `searcher.py` | Scans the local folders in `MP3_FOLDERS`, searches YouTube for the source video of each MP3, scores the candidates, and writes the best match per file to `matches.csv`. |
| `filter_local_quality.py` | Flags rows whose local mp3 is already ≥ 192 kbps (so the YouTube re-download is unwanted), adds `local_bitrate` / `local_better` columns, and writes a filtered download list to `ids2.txt`. |
| `acoustid_enrich.py` | Cross-checks each local mp3 against the AcoustID + MusicBrainz database (Picard's engine): fingerprints the audio, looks it up, and writes canonical `mb_artist`/`mb_title`/`mb_recording_id`/`ac_score` plus an `mb_confidence` + `mb_suggest` cross-check vs the YouTube match. Language-independent, so it catches wrong Japanese matches. Needs `fpcalc`, `pyacoustid`, and `ACOUSTID_API_KEY` (see review_app/README). Resumable. |
| `mb_enrich.py` | Text-search fallback to `acoustid_enrich.py`: looks each `matches.csv` row up in MusicBrainz by `artist`/`title` (no audio, no fpcalc, no key) and fills the same `mb_*` columns on rows the fingerprint pass left blank. Resumable. |
| `lyrics_fetch.py` | Fetches lyrics for each mp3 in `MP3_FOLDERS` (LRCLIB first, then NetEase / Kugou / J-Lyric fallbacks) and writes a `.lrc` (synced) or `.txt` sidecar next to the file. Skips files that already have one. Resumable. |
| `tag_enrich.py` | Fixes garbage YouTube metadata on downloaded files: text-searches MusicBrainz by artist/title and, when confident, **writes** canonical title/artist/album/date/genre back into the file tags (mp3/m4a/opus/flac/ogg) — and optionally embeds lyrics too. The write-back the `*_enrich` scripts skip (they only touch `matches.csv`). Resumable via `tag_enriched.txt`. |
| `cleanup_downloads.py` | Deletes failed, partial, and zero-byte files left in `downloads/` and removes their IDs from `downloaded_ids.txt`. |
| `check_untracked.py` | Lists library files not yet verified in `matches.csv`, writing them to `untracked.txt`. |
| `cleanup_tracked.py` | Deletes source MP3s that are already verified in `matches.csv`. |

`cleanup_csv.py` and `remove_index.py` are one-off helpers for repairing malformed match CSVs.

## How the pieces fit together

The scripts don't call each other — they pass data through shared files. Two main flows:

- **Build / download:** `dump.csv` → `url_extractor.py` → ID and URL lists → `downloader.py` → audio in `downloads/`.
- **Match an existing library:** `searcher.py` → `matches.csv` → (optionally `filter_local_quality.py` and `acoustid_enrich.py` to enrich) → curate (web app or by hand) → `check_untracked.py` / `cleanup_tracked.py`.

Downloading and searching are resumable: re-running skips IDs already logged as done, so an interrupted run picks up where it stopped.

## Curation web app (`review_app/`)

Reviewing thousands of matches in a spreadsheet is slow. `review_app/` is a small FastAPI + SQLite + Vue/Vuetify app that plays your local mp3 next to the YouTube candidate (IFrame embed plus an audio-only preview, so you can verify by ear even when the embed is blocked) and the MusicBrainz cross-check, so you can approve/reject by ear, one keystroke each. It imports `matches.csv`/`matches.xlsx`, stores curation in SQLite, and exports back to both files (snapshotting backups first). See [`review_app/README.md`](review_app/README.md) for setup, run, and tests.

Shortcut: `setup.bat` and `run.bat` at the repo root just `cd review_app` and forward to `install.py` / `run.py` (e.g. `run.bat --dev`), so you can install and launch the app without changing directories.

`matches.csv` and `matches.xlsx` hold the curation (the `check` column) and are committed to git so the marks are versioned; the `matches - Copy*` files are old manual backups.

Beyond reviewing, the app now hosts the rest of the toolkit so you don't have to drop to a shell:

- **Discord tab** — fetch a channel's YouTube links straight into the pipeline (`ids.txt`/`urls.txt`/`playlists.txt`).
- **Pipeline tab** — run any root script (downloader, searcher, enrich, cleanup, …) as a background job and watch its log. Destructive scripts (the `cleanup_*` deleters) are gated behind a typed `DELETE` confirmation.
- **Settings tab** — store secrets (Discord bot token, AcoustID key) in a gitignored `.env` at the repo root; the app applies them to the environment so launched scripts inherit them.

## Liking videos back (`extension/`)

`extension/` is a Chrome extension that likes a list of harvested video IDs on **your** YouTube account from your logged-in browser session (no OAuth). It reads `ids.txt` from the running app (`/api/likes/queue`) or a pasted list, and likes them throttled with a Stop button. Bulk liking can trip spam limits — see [`extension/README.md`](extension/README.md).

### Sign-in / age-restricted videos

Some videos require a logged-in account. When yt-dlp reports one, its URL is appended to `sign_in.txt` and retried using cookies from `cookies.txt` (a cookies file you export from your browser). Setting the `sign_in_only` flag in a script makes it re-run over just those URLs with cookies.

## Notes

- Paths in `MP3_FOLDERS` are absolute Windows drive paths (`E:/...`); change them for your own machine.
- Files like `matches - Copy*.csv`, `*.bak`, and `tmp*.tmp` are manual backups, not generated output.

## License

See `LICENSE.txt`.
