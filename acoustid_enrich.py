"""
Cross-check matches against the "Picard database" (AcoustID + MusicBrainz).

For each local mp3 it fingerprints the audio (fpcalc), looks the fingerprint up
on AcoustID, and records the canonical MusicBrainz recording. Because the match
comes from the AUDIO, it is language-independent -- the fix for wrong Japanese
matches that romaji fuzzy-matching can't solve.

Adds these columns to matches.csv (non-core -> they ride along in the review
app's extra_json automatically):
    mb_recording_id   MusicBrainz recording MBID (blank = no AcoustID match)
    mb_artist         canonical artist from MusicBrainz
    mb_title          canonical title
    ac_score          AcoustID match confidence 0..1
    ac_done           1 once a file has been attempted (makes the run resumable)
    mb_confidence     strong | weak | none  -- does the YouTube candidate agree
    mb_suggest        1 when strong AND high AcoustID score -> auto-approve hint

Setup: see review_app/README or the project docs. Needs fpcalc on PATH,
`pip install pyacoustid`, and the ACOUSTID_API_KEY environment variable.

Run (resumable): python acoustid_enrich.py
"""
import os
import sys
import time

import pandas as pd

MATCHES_CSV = "matches.csv"
MATCHES_XLSX = "matches.xlsx"
MP3_FOLDERS = [
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
]

API_KEY = os.environ.get("ACOUSTID_API_KEY")
RATE_LIMIT_S = 0.34        # AcoustID asks for <= 3 requests/second
SAVE_EVERY = 25            # checkpoint matches.csv this often
AC_STRONG = 0.5            # AcoustID score considered confident
TITLE_THR = 70            # fuzzy thresholds for the YT-vs-MB cross-check
ARTIST_THR = 60

NEW_COLUMNS = ["mb_recording_id", "mb_artist", "mb_title", "ac_score",
               "ac_done", "mb_confidence", "mb_suggest"]


# --- text matching (romaji-aware, language tolerant) ---------------------
try:
    import pykakasi
    _kks = pykakasi.kakasi()

    def romanize(s):
        return " ".join(i["hepburn"] for i in _kks.convert(str(s)))
except Exception:                       # pykakasi optional; fall back to identity
    def romanize(s):
        return str(s)

from rapidfuzz.fuzz import partial_ratio


def _ratio(a, b):
    if not a or not b:
        return 0.0
    return partial_ratio(romanize(a).lower(), romanize(b).lower())


# --- pure confidence logic (unit-tested) ---------------------------------

def match_confidence(mb_artist, mb_title, yt_channel, yt_title, ac_score,
                     title_thr=TITLE_THR, artist_thr=ARTIST_THR, ac_strong=AC_STRONG):
    """Does the YouTube candidate agree with the AcoustID/MusicBrainz hit?

    Returns (confidence, suggest) where confidence is 'strong'|'weak'|'none'
    and suggest is True when it's safe to auto-approve (strong + high ac_score).
    """
    if not mb_title and not mb_artist:
        return "none", False
    title_sim = _ratio(mb_title, yt_title)
    artist_sim = max(_ratio(mb_artist, yt_channel), _ratio(mb_artist, yt_title))

    strong = title_sim >= title_thr and artist_sim >= artist_thr
    if strong:
        suggest = ac_score is not None and ac_score >= ac_strong
        return "strong", suggest
    if title_sim >= title_thr or artist_sim >= artist_thr:
        return "weak", False
    return "none", False


# --- IO / fingerprinting -------------------------------------------------

def build_file_index(folders):
    index = {}
    for folder in folders:
        if not os.path.isdir(folder):
            print(f"Folder missing, skipping: {folder}")
            continue
        for name in os.listdir(folder):
            if name.lower().endswith(".mp3") and name not in index:
                index[name] = os.path.join(folder, name)
    return index


def lookup_acoustid(path):
    """Top AcoustID/MusicBrainz hit for a file: dict or None. Network."""
    import acoustid
    try:
        for score, rid, title, artist in acoustid.match(API_KEY, path):
            return {"mb_recording_id": rid, "mb_artist": artist,
                    "mb_title": title, "ac_score": score}
        return {}                       # fingerprinted, but no DB match
    except acoustid.NoBackendError:
        sys.exit("fpcalc (Chromaprint) not found on PATH. Install it first.")
    except (acoustid.FingerprintGenerationError, acoustid.WebServiceError) as e:
        print(f"AcoustID error for {os.path.basename(path)}: {e}")
        return {}


def main():
    if not API_KEY:
        sys.exit("Set ACOUSTID_API_KEY (see setup docs) before running.")

    df = pd.read_csv(MATCHES_CSV)
    for col in NEW_COLUMNS:
        if col not in df.columns:
            df[col] = None
    index = build_file_index(MP3_FOLDERS)

    done = matched = 0
    for i, row in df.iterrows():
        if row.get("ac_done") in (1, 1.0, True, "True"):
            continue                    # resumable: skip already-attempted
        path = index.get(row["filename"])
        if not path:
            continue

        hit = lookup_acoustid(path)
        df.at[i, "ac_done"] = 1
        if hit:
            matched += 1
            for k, v in hit.items():
                df.at[i, k] = v
            conf, suggest = match_confidence(
                hit.get("mb_artist"), hit.get("mb_title"),
                row.get("yt_channel"), row.get("yt_title"), hit.get("ac_score"))
            df.at[i, "mb_confidence"] = conf
            df.at[i, "mb_suggest"] = 1 if suggest else 0
            print(f"[{i}] {hit.get('mb_artist')} - {hit.get('mb_title')}  "
                  f"({hit.get('ac_score'):.2f}, {conf})")

        done += 1
        if done % SAVE_EVERY == 0:
            df.to_csv(MATCHES_CSV, index=False)
            print(f"  ...checkpoint ({done} attempted, {matched} matched)")
        time.sleep(RATE_LIMIT_S)

    df.to_csv(MATCHES_CSV, index=False)
    try:
        df.to_excel(MATCHES_XLSX, index=False)
    except Exception as e:
        print(f"Skipped writing {MATCHES_XLSX}: {e}")
    print(f"Done. Attempted {done}, matched {matched}.")


if __name__ == "__main__":
    main()
