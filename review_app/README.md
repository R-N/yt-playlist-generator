# Match Review app

Local web UI to curate `matches.csv`: play your local mp3 next to the YouTube
candidate, approve/reject with one key. Replaces hand-editing the spreadsheet.

## Stack
- **Backend:** FastAPI + SQLite (`backend/`)
- **Frontend:** Vue 3 + Vuetify 3 via Vite (`frontend/`)
- Local audio served read-only with HTTP Range (scrubbing works); YouTube
  candidate shown via the official IFrame embed.

## Data safety (this is the point)
- Your verified `check` marks are the irreplaceable asset. The app guards them:
  - SQLite store with ACID transactions — no half-written file ever.
  - `decisions` table is **append-only**: every approve/reject is logged with a
    timestamp and never overwritten. `tracks.check` is the current value;
    `decisions` is the replayable history if anything looks wrong.
  - `Export CSV` snapshots the old `matches.csv`/`.xlsx` into `backups/` first,
    then writes via temp-file + atomic rename.
  - The app never writes or deletes any audio file.
- On first launch it imports `matches.csv` once (only if the DB is empty), so
  your existing marks are preserved, not reset.

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
  atomic decisions, snapshot-backed atomic export, and non-core column
  (`extra_json`) round-trip.
- `test_api` — endpoints, the auto-export-every-N trigger, and the audio
  endpoint's read-only / `Range` (206) / 404 behavior.

Frontend logic (Vitest) — pure helpers in `src/review.js` (key mapping, queue
advance, formatting, embed URL):
```bash
cd review_app/frontend
npm run test
```

## Re-seeding the DB
The DB imports `matches.csv` only when empty. To re-import after changing the
CSV, delete `backend/review.db` (and `-wal`/`-shm`) and restart — **export
first** so you don't lose marks made only in the DB.
