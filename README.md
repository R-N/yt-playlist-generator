# yt-playlist-generator

A small set of Python scripts for managing a YouTube-sourced music library. The original tool just turns a list of video URLs into a YouTube playlist link; the rest of the scripts grew around downloading audio, matching local MP3s back to their YouTube source, and cleaning up the results.

Each script is standalone — run it directly with `python <script>.py`. There is no install step. Configuration lives in constants at the top of each file (input/output filenames, the `MP3_FOLDERS` list, mode flags); edit those to change behavior.

## Requirements

- Python 3
- `pip install yt-dlp pandas mutagen rapidfuzz pykakasi`
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
| `downloader.py` | Reads video IDs from `ids.txt` and downloads each as audio (Opus) into `downloads/`, with thumbnail and metadata embedded. Tracks progress in `downloaded_ids.txt` / `error_ids.txt`. |
| `searcher.py` | Scans the local folders in `MP3_FOLDERS`, searches YouTube for the source video of each MP3, scores the candidates, and writes the best match per file to `matches.csv`. |
| `cleanup_downloads.py` | Deletes failed, partial, and zero-byte files left in `downloads/` and removes their IDs from `downloaded_ids.txt`. |
| `check_untracked.py` | Lists library files not yet verified in `matches.csv`, writing them to `untracked.txt`. |
| `cleanup_tracked.py` | Deletes source MP3s that are already verified in `matches.csv`. |

`cleanup_csv.py` and `remove_index.py` are one-off helpers for repairing malformed match CSVs.

## How the pieces fit together

The scripts don't call each other — they pass data through shared files. Two main flows:

- **Build / download:** `dump.csv` → `url_extractor.py` → ID and URL lists → `downloader.py` → audio in `downloads/`.
- **Match an existing library:** `searcher.py` → `matches.csv` → `check_untracked.py` / `cleanup_tracked.py`.

Downloading and searching are resumable: re-running skips IDs already logged as done, so an interrupted run picks up where it stopped.

### Sign-in / age-restricted videos

Some videos require a logged-in account. When yt-dlp reports one, its URL is appended to `sign_in.txt` and retried using cookies from `cookies.txt` (a cookies file you export from your browser). Setting the `sign_in_only` flag in a script makes it re-run over just those URLs with cookies.

## Notes

- Paths in `MP3_FOLDERS` are absolute Windows drive paths (`E:/...`); change them for your own machine.
- Files like `matches - Copy*.csv`, `*.bak`, and `tmp*.tmp` are manual backups, not generated output.

## License

See `LICENSE.txt`.
