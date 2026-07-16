"""
Fix garbage YouTube metadata on downloaded files: look each file up in MusicBrainz
by artist/title and WRITE the canonical tags back into the file (and, optionally,
embed lyrics). This is the write-back the enrich scripts don't do -- acoustid_enrich
/ mb_enrich only record mb_* columns in matches.csv; this edits the audio tags.

For each file it: reads the current artist/title (falling back to parse_title on the
filename when tags are blank), text-searches MusicBrainz, scores the candidates, and
-- only when the best clears CONFIDENCE_BAR -- embeds title/artist/album/albumartist/
date/genre. With EMBED_LYRICS it also fetches lyrics (lyrics_fetch) and embeds them,
falling back to a .lrc/.txt sidecar for containers that can't hold a lyrics tag.

Resumable: every processed filename is appended to tag_enriched.txt and skipped next
run (delete the log to force a redo). No key needed -- MusicBrainz is anonymous at
<= 1 req/sec (honored below). Reuses the tested helpers in acoustid_enrich / mb_enrich
/ searcher / lyrics_fetch rather than re-deriving them.

Run (resumable): python tag_enrich.py
"""
import json
import os
import time
import urllib.parse
import urllib.request

import mutagen

import lyrics_fetch
from acoustid_enrich import _ratio                 # romaji-aware fuzzy ratio (0..100)
from mb_enrich import _artist_name                 # MusicBrainz artist-credit joiner
from searcher import parse_title                   # offline messy-title -> (artist, title)

FOLDERS = ["downloads"]                            # where downloader.py writes; add MP3_FOLDERS if wanted
DONE_LOG = "tag_enriched.txt"
RATE_LIMIT_S = 1.1                                 # MusicBrainz: <= 1 request/second
EMBED_LYRICS = True
OVERWRITE = True                                   # YT tags are garbage -> replace them when confident
UA = "yt-playlist-generator/1.0 (personal music library)"
AUDIO_EXTS = (".opus", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".wav")

_TAG_FIELDS = ("title", "artist", "album", "albumartist", "date", "genre")
_MP4_MAP = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
            "albumartist": "aART", "date": "\xa9day", "genre": "\xa9gen"}


# --- pure logic (unit-tested) --------------------------------------------

def score_candidate(cand_artist, cand_title, guess_artist, guess_title):
    """Confidence 0..1 that a MusicBrainz candidate is the same song as the guess.
    Title weighs more than artist (YT's 'artist' is the weakest field); a strong title
    alone can carry a weak artist. Ported from usb-ldac's ytmeta.score_candidate."""
    t = _ratio(cand_title, guess_title) / 100.0
    a = (_ratio(cand_artist, guess_artist) / 100.0) if guess_artist else t
    return round(0.65 * t + 0.35 * a, 3)


# Above this, take the MusicBrainz tags; below, leave the file untouched. Tunable.
CONFIDENCE_BAR = 0.72


def parse_recording_full(row):
    """One MusicBrainz recording JSON object -> full tag dict (title/artist/album/
    albumartist/date/genre). Richer than mb_enrich.parse_recording, which only needs id."""
    release = (row.get("releases") or [{}])[0]
    tags = {
        "title": row.get("title") or "",
        "artist": _artist_name(row.get("artist-credit")),
        "album": release.get("title") or "",
        "albumartist": _artist_name(release.get("artist-credit")),
        "date": release.get("date") or row.get("first-release-date") or "",
        "genre": (row.get("tags") or [{}])[0].get("name") or "",
    }
    return {"id": row.get("id") or "", "score": row.get("score") or 0,
            "tags": {k: v for k, v in tags.items() if v}}


def pick_best(candidates, guess_artist, guess_title):
    """Highest-scoring candidate and its confidence (0..1)."""
    best, best_score = None, 0.0
    for c in candidates:
        tags = c.get("tags") or {}
        s = score_candidate(tags.get("artist", ""), tags.get("title", ""), guess_artist, guess_title)
        if s > best_score:
            best, best_score = c, s
    return best, best_score


# --- network -------------------------------------------------------------

def mb_search(artist, title, limit=5):
    """MusicBrainz recording candidates for an artist/title (list of parse_recording_full)."""
    terms = []
    for field, value in (("artist", artist), ("recording", title)):
        value = (value or "").strip()
        if value:
            terms.append(f'{field}:"{value.replace(chr(34), "")}"')
    if not terms:
        return []
    params = urllib.parse.urlencode({"query": " AND ".join(terms), "fmt": "json", "limit": str(limit)})
    url = f"https://musicbrainz.org/ws/2/recording/?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace")).get("recordings", [])
    except Exception as e:
        print(f"  MB error: {e}")
        return []
    return [parse_recording_full(r) for r in rows]


# --- tag / lyrics embedding ----------------------------------------------

def embed_tags(path, tags):
    """Write standard tags into an audio file. Returns True on success. Branches by
    container: MP3 (EasyID3), M4A/MP4 (atom map), else Vorbis comments (opus/ogg/flac)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.easyid3 import EasyID3
            from mutagen.mp3 import MP3
            audio = MP3(path, ID3=EasyID3)
            if audio.tags is None:
                audio.add_tags()
            for k, v in tags.items():
                if v:
                    audio[k] = [v]
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(path)
            for k, v in tags.items():
                if v and k in _MP4_MAP:
                    audio[_MP4_MAP[k]] = [v]
        else:                                   # flac / ogg / opus -> vorbis comments
            audio = mutagen.File(path)
            if audio is None:
                return False
            if audio.tags is None:
                audio.add_tags()
            for k, v in tags.items():
                if v:
                    audio[k] = [v]
        audio.save()
        return True
    except Exception as e:
        print(f"  tag write failed: {e}")
        return False


def embed_lyrics(path, lyrics):
    """Embed lyrics into an audio file. Returns True on success, False if the container
    can't hold them (caller then writes a sidecar)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, USLT
            try:
                tags = ID3(path)
            except Exception:
                tags = ID3()
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="und", desc="", text=lyrics))
            tags.save(path)
        elif ext in (".m4a", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(path)
            audio["\xa9lyr"] = [lyrics]
            audio.save()
        else:                                   # vorbis comments
            audio = mutagen.File(path)
            if audio is None:
                return False
            if audio.tags is None:
                audio.add_tags()
            audio["LYRICS"] = [lyrics]
            audio.save()
        return True
    except Exception:
        return False


# --- IO / resume ---------------------------------------------------------

def load_done():
    if os.path.exists(DONE_LOG):
        with open(DONE_LOG, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def mark_done(name):
    with open(DONE_LOG, "a", encoding="utf-8") as f:
        f.write(name + "\n")


def current_tags(path):
    """(artist, title, duration) from a file's existing tags; blanks/0 on failure."""
    try:
        audio = mutagen.File(path, easy=True)
        artist = (audio.get("artist") or [""])[0]
        title = (audio.get("title") or [""])[0]
        dur = getattr(getattr(audio, "info", None), "length", 0) or 0
        return artist.strip(), title.strip(), dur
    except Exception:
        return "", "", 0


def main():
    done = load_done()
    tagged = lyriced = 0
    for folder in FOLDERS:
        if not os.path.isdir(folder):
            print(f"Folder missing, skipping: {folder}")
            continue
        print("Processing folder", folder)
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(AUDIO_EXTS) or name in done:
                continue
            path = os.path.join(folder, name)

            artist, title, dur = current_tags(path)
            if not (artist and title):                 # garbage/blank tags -> parse the filename
                g_artist, g_title = parse_title(os.path.splitext(name)[0])
                artist = artist or g_artist
                title = title or g_title
            if not title:
                mark_done(name)
                continue

            best, conf = pick_best(mb_search(artist, title), artist, title)
            final_artist, final_title = artist, title
            if best and conf >= CONFIDENCE_BAR:
                if OVERWRITE and embed_tags(path, best["tags"]):
                    tagged += 1
                    final_artist = best["tags"].get("artist") or artist
                    final_title = best["tags"].get("title") or title
                    print(f"[tag] {name} -> {final_artist} - {final_title}  ({conf})")
            else:
                print(f"[   ] {name}: no confident MB match ({conf})")

            if EMBED_LYRICS:
                lyrics = lyrics_fetch.fetch_lyrics(final_artist, final_title, dur)
                if lyrics:
                    if embed_lyrics(path, lyrics) or lyrics_fetch.write_sidecar(path, lyrics):
                        lyriced += 1

            mark_done(name)
            time.sleep(RATE_LIMIT_S)

    print(f"Done. Tagged {tagged}, lyrics {lyriced}.")


if __name__ == "__main__":
    main()
