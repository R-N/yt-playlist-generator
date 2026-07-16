"""
Text-search MusicBrainz to fill mb_* columns from artist/title (no audio needed).

Companion/fallback to acoustid_enrich.py. That one fingerprints the AUDIO -- stronger,
but needs the files present, fpcalc, and an API key. This one text-searches the
MusicBrainz recording DB using the artist/title already in matches.csv, so it works on
rows whose audio is gone or that fpcalc can't fingerprint. It only fills rows where
mb_recording_id is still blank -- it never overwrites a fingerprint result.

Writes the same mb_* columns acoustid_enrich uses (so the review app's extra_json
picks them up), plus:
    mb_text_score   MusicBrainz search score 0..100
    mbt_done        1 once a row was attempted (resumable)
    mb_source       'text' on rows this script filled

No key needed -- MusicBrainz allows anonymous queries at <= 1 req/sec (honored below).

Run (resumable): python mb_enrich.py
"""
import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from acoustid_enrich import match_confidence   # reuse the tested YT<->MB cross-check

MATCHES_CSV = "matches.csv"
MATCHES_XLSX = "matches.xlsx"
RATE_LIMIT_S = 1.1        # MusicBrainz: <= 1 request/second for anonymous clients
SAVE_EVERY = 25
UA = "yt-playlist-generator/1.0 (personal music library)"

NEW_COLUMNS = ["mb_recording_id", "mb_artist", "mb_title", "mb_text_score",
               "mbt_done", "mb_source", "mb_confidence", "mb_suggest"]


# --- pure logic (unit-tested) --------------------------------------------

def _artist_name(credit):
    """Join a MusicBrainz artist-credit array into one display string."""
    return "".join(
        f"{p.get('name') or p.get('artist', {}).get('name', '')}{p.get('joinphrase', '')}"
        for p in (credit or [])
    )


def parse_recording(row):
    """One MusicBrainz recording JSON object -> flat mb_* dict."""
    return {
        "mb_recording_id": row.get("id") or "",
        "mb_title": row.get("title") or "",
        "mb_artist": _artist_name(row.get("artist-credit")),
        "mb_text_score": row.get("score") or 0,
    }


def _blank(v):
    return v is None or v == "" or (isinstance(v, float) and pd.isna(v))


# --- network -------------------------------------------------------------

def search_recording(artist, title):
    """Top MusicBrainz recording for an artist/title, as a flat dict ({} if none)."""
    terms = []
    for field, value in (("artist", artist), ("recording", title)):
        value = (value or "").strip()
        if value:
            terms.append(f'{field}:"{value.replace(chr(34), "")}"')
    if not terms:
        return {}
    params = urllib.parse.urlencode({"query": " AND ".join(terms), "fmt": "json", "limit": "1"})
    url = f"https://musicbrainz.org/ws/2/recording/?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace")).get("recordings", [])
    except Exception as e:
        print(f"  MB error: {e}")
        return {}
    return parse_recording(rows[0]) if rows else {}


def main():
    df = pd.read_csv(MATCHES_CSV)
    for col in NEW_COLUMNS:
        if col not in df.columns:
            df[col] = None

    done = matched = 0
    for i, row in df.iterrows():
        if row.get("mbt_done") in (1, 1.0, True, "True"):
            continue
        if not _blank(row.get("mb_recording_id")):
            df.at[i, "mbt_done"] = 1          # already has a (fingerprint) match; leave it
            continue
        artist, title = row.get("artist"), row.get("title")
        if _blank(title) and _blank(artist):
            df.at[i, "mbt_done"] = 1
            continue

        hit = search_recording(str(artist or ""), str(title or ""))
        df.at[i, "mbt_done"] = 1
        if hit and hit["mb_recording_id"]:
            matched += 1
            for k, v in hit.items():
                df.at[i, k] = v
            df.at[i, "mb_source"] = "text"
            # ac_score=None -> a text match never earns an auto-approve suggestion.
            conf, suggest = match_confidence(
                hit["mb_artist"], hit["mb_title"],
                row.get("yt_channel"), row.get("yt_title"), None)
            df.at[i, "mb_confidence"] = conf
            df.at[i, "mb_suggest"] = 1 if suggest else 0
            print(f"[{i}] {hit['mb_artist']} - {hit['mb_title']}  ({hit['mb_text_score']}, {conf})")

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
