# Match Review app

Local web UI to curate `matches.csv`: play your local mp3 next to the YouTube
candidate, approve/reject with one key. Replaces hand-editing the spreadsheet.

## Stack
- **Backend:** FastAPI + SQLite (`backend/`)
- **Frontend:** Vue 3 + Vuetify 3 via Vite (`frontend/`)
- Local audio served read-only with HTTP Range (scrubbing works); YouTube
  candidate shown via the official IFrame embed **and** an audio-only preview
  (`GET /api/yt_audio/{yt_id}` resolves the candidate's audio with `yt-dlp -g`
  and redirects the `<audio>` element to it) so you can verify by ear even when
  the embed is blocked (age-restricted / embedding disabled).

## Tabs

The UI is tabbed; the original review screen is one of them.

- **Review** — curate matches by ear (the rest of this README).
- **Discord** — fetch a Discord channel's messages via the bot API, extract every
  YouTube video id, and write `ids.txt`/`urls.txt`/`playlists.txt` at the repo
  root (feeds the downloader / playlist flow). Backed by `discord_service.py`,
  which reuses the repo-root `discord_fetch.py` + `discord_extractor.py`.
- **Pipeline** — run any root script as a background subprocess job and watch its
  log live; Stop cancels it. The scripts are unchanged — the app orchestrates
  them (this keeps the repo's "scripts share files as their interface" design).
  Destructive scripts (`cleanup_downloads`, `cleanup_tracked` — they delete
  files) are flagged red and require typing `DELETE` before they run.
- **Settings** — store secrets in a gitignored `.env` at the repo root
  (`DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `ACOUSTID_API_KEY`). On startup the
  backend loads `.env` into the environment (without clobbering real env vars),
  so the subprocess scripts inherit them. Secrets are masked in the API.

A companion Chrome extension (`../extension/`) consumes `ids.txt` via
`GET /api/likes/queue` to like the harvested videos on your account — see
`extension/README.md`.

## Data safety (this is the point)
- Your verified `check` marks are the irreplaceable asset. The app guards them:
  - SQLite store with ACID transactions — no half-written file ever.
  - `decisions` table is **append-only**: every approve/reject is logged with a
    timestamp and never overwritten. `tracks.check` is the current value;
    `decisions` is the replayable history if anything looks wrong.
  - `Export CSV` snapshots the old `matches.csv`/`.xlsx` into `backups/` first,
    then writes via temp-file + atomic rename.
  - The app never writes or deletes any audio file.
- On first launch it reconciles `matches.csv` + `matches.xlsx` into the DB once
  (only if empty) — union of rows, xlsx-priority marks, never dropping a
  decision — so your existing marks are preserved, not reset.

Native wrappers (`.bat`, `.ps1`, `.sh`) wrap the Python scripts and forward all
args. Use whichever fits your shell; they do the same thing.

## Setup (one-time)
Run from the env that has pandas (e.g. mambaforge), so backend deps land there:
```
cd review_app
install.bat                 cmd
.\install.ps1               PowerShell
./install.sh                bash / git-bash
python install.py           any
```
`--backend` / `--frontend` install just one side.

## Run
```
cd review_app
run.bat                     cmd          (built mode, default)
.\run.ps1 --dev             PowerShell   (dev mode)
./run.sh                    bash
python run.py               any
```
Built mode (default): build frontend, serve SPA + API on :8000. `--dev`: uvicorn
`--reload` + Vite on :5173. `run.py` auto-runs `npm install` if you skipped setup.
- Built mode (default): open <http://127.0.0.1:8000>. Rebuilds each run; add
  `--no-build` to serve the existing `frontend/dist/` as-is.
- Dev mode: open <http://localhost:5173> (Vite hot-reload; proxies `/api`).
- Options: `--port N`, `--host H`, `--no-install`.

Review, then click **Export CSV** to write your marks back to `matches.csv` /
`matches.xlsx` (marks also auto-save to the CSV every 25 decisions).

## Keys
`A` / `→` approve · `R` / `←` reject · `↑` back

## AcoustID / MusicBrainz cross-check (optional)
`acoustid_enrich.py` (repo root) fingerprints each local mp3 and tags it with
the canonical MusicBrainz artist/title — language-independent, so it catches
wrong Japanese matches. The review UI shows it as a MusicBrainz panel
(green/amber/grey = strong/weak/none) and flags `suggests APPROVE` when AcoustID
is confident and the YouTube candidate agrees.

Setup (in your mambaforge env):
1. Free API key — register an app at <https://acoustid.org/new-application>.
2. `conda install -c conda-forge chromaprint` (provides `fpcalc`; verify `fpcalc -version`).
3. `pip install pyacoustid musicbrainzngs`
4. Expose the key: `export ACOUSTID_API_KEY=YOURKEY` (bash) /
   `$env:ACOUSTID_API_KEY = "YOURKEY"` (PowerShell).
5. `python acoustid_enrich.py` (resumable), then **re-seed** the review DB
   (delete `backend/review.db`) so the new `mb_*` columns import.

## Tests
`unittest` (stdlib). Every test redirects DB/CSV/XLSX paths and `MP3_FOLDERS`
to a temp dir, so real `matches.*` and music files are never touched.
```bash
cd review_app/backend
python -m unittest discover -p "test_*.py" -v     # all
python -m unittest test_db -v                     # data layer only
python -m unittest test_api -v                    # API only (needs httpx)
```
- `test_db` — `check` coercion, reconcile (union, xlsx-priority, blank-rescue,
  conflict count, no-marks-lost, csv/xlsx-only, dup-filename), append-only
  atomic decisions, snapshot-backed atomic export, non-core column
  (`extra_json`) round-trip, and the NaN scrub in `_expand_extra` (blank numeric
  cells arrive as NaN, which the JSON encoder rejects).
- `test_api` — endpoints, the auto-export-every-N trigger, the audio endpoint's
  read-only / `Range` (206) / 404 behavior, the `/api/yt_audio` candidate-preview
  redirect (id validation, resolver stubbed), and the NaN-serialization guard on
  `/api/rows` (a NaN in the real DB must serialize, not 500).
- `test_integrations` — settings `.env` round-trip + secret masking, the Discord
  service (extraction order, embeds, author filter, missing-token error; network
  stubbed), and the job catalog's destructive flags.

Frontend logic (Vitest) — pure helpers in `src/review.js` (key mapping, queue
advance, formatting, embed URL):
```bash
cd review_app/frontend
npm run test
```

## Re-seeding the DB
The DB reconciles `matches.csv` + `matches.xlsx` only when empty. To re-import
after changing them (e.g. after `acoustid_enrich.py`), delete `backend/review.db`
(and `-wal`/`-shm`) and restart — **export first** so you don't lose marks made
only in the DB.
