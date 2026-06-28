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

## Run
Backend (use the Python env that has pandas, e.g. your mambaforge):
```bash
cd review_app/backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Frontend (separate terminal):
```bash
cd review_app/frontend
npm install
npm run dev          # opens http://localhost:5173
```
Review, then click **Export CSV** to write your marks back to `matches.csv`.
For a single-process build instead of the dev server: `npm run build` (creates
`frontend/dist/`, which the backend then serves at `http://localhost:8000`).

## Keys
`A` / `→` approve · `R` / `←` reject · `↑` back

## Re-seeding the DB
The DB imports `matches.csv` only when empty. To re-import after changing the
CSV, delete `backend/review.db` (and `-wal`/`-shm`) and restart — **export
first** so you don't lose marks made only in the DB.
