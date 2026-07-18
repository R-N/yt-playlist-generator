"""
Fetch lyrics for local mp3s from LRCLIB and write .lrc/.txt sidecars.

Scans MP3_FOLDERS, reads each file's artist/title/duration tags, queries LRCLIB,
and writes a sidecar next to the file: <name>.lrc when the result is time-synced,
else <name>.txt. Resumable -- a file that already has a sidecar is skipped.

Providers, in order: LRCLIB (free, no key, best general coverage), then NetEase,
Kugou, and J-Lyric.net (Japanese) as fallbacks for catalogue LRCLIB misses.

ponytail: the NetEase/Kugou/J-Lyric providers hit undocumented endpoints and scrape
HTML, so a site change silently returns nothing. That's acceptable here -- every
fallback is best-effort and its failure is swallowed; LRCLIB stays the primary.

Run (resumable): python lyrics_fetch.py
"""
import base64
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

import mutagen

from folder_config import resolve_mp3_folders

MP3_FOLDERS = resolve_mp3_folders([
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
])
RATE_LIMIT_S = 0.5
UA = "yt-playlist-generator/1.0 (personal music library)"


# --- pure logic (unit-tested) --------------------------------------------

def is_synced(lyrics):
    """True if the lyrics carry LRC timestamps like [00:12.34]."""
    return bool(re.search(r"(?m)^\[\d+:\d+", lyrics or ""))


def pick_lyrics(record):
    """Pull the best lyrics out of one LRCLIB record: synced > plain, '' if none
    or the track is flagged instrumental."""
    if not isinstance(record, dict) or record.get("instrumental"):
        return ""
    return record.get("syncedLyrics") or record.get("plainLyrics") or ""


# --- IO / network --------------------------------------------------------

def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _get_text(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("utf-8", "replace")


def _html_to_text(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# NetEase/Kugou expose no official API; these are the endpoints the community clients
# (NeteaseCloudMusicApi, KuGou-Music-API) use. Best-effort -- any failure returns "".
_NETEASE_HEADERS = {"Referer": "https://music.163.com", "Cookie": "appver=8.7.01"}


def _netease_lyrics(query):
    url = "https://music.163.com/api/search/get?" + urllib.parse.urlencode({"s": query, "type": 1, "limit": 3})
    songs = ((_get_json(url, _NETEASE_HEADERS) or {}).get("result") or {}).get("songs") or []
    for song in songs[:3]:
        lyric_url = "https://music.163.com/api/song/lyric?" + urllib.parse.urlencode(
            {"id": song.get("id"), "lv": 1, "kv": 1, "tv": -1})
        try:
            lyrics = ((_get_json(lyric_url, _NETEASE_HEADERS) or {}).get("lrc") or {}).get("lyric") or ""
        except Exception:
            continue
        if lyrics.strip():
            return lyrics
    return ""


def _kugou_lyrics(query):
    # Three hops per song (search -> candidate -> download), so keep the fan-out small.
    url = "http://mobilecdn.kugou.com/api/v3/search/song?" + urllib.parse.urlencode(
        {"format": "json", "keyword": query, "page": 1, "pagesize": 3})
    songs = ((_get_json(url) or {}).get("data") or {}).get("info") or []
    for song in songs[:3]:
        song_hash = song.get("hash")
        if not song_hash:
            continue
        try:
            search_url = "http://krcs.kugou.com/search?" + urllib.parse.urlencode(
                {"ver": 1, "man": "yes", "client": "mobi", "hash": song_hash})
            candidates = (_get_json(search_url) or {}).get("candidates") or []
            if not candidates:
                continue
            best = candidates[0]
            download_url = "http://lyrics.kugou.com/download?" + urllib.parse.urlencode(
                {"ver": 1, "client": "pc", "id": best.get("id"), "accesskey": best.get("accesskey"),
                 "fmt": "lrc", "charset": "utf8"})
            content = (_get_json(download_url) or {}).get("content") or ""
            lyrics = base64.b64decode(content).decode("utf-8", "replace")
        except Exception:
            continue
        if lyrics.strip():
            return lyrics
    return ""


def _jlyric_lyrics(title):
    """J-Lyric.net -- plain-text Japanese lyrics (no timestamps), scraped from HTML."""
    if not title:
        return ""
    url = "https://j-lyric.net/search.php?" + urllib.parse.urlencode({"kt": title, "ct": 2, "ka": "", "ca": 2})
    page = _get_text(url)
    for href, _t in re.findall(r'<a href="(/artist/a[^"/]+/l[^"]+\.html)"[^>]*>([^<]+)</a>', page)[:3]:
        try:
            song = _get_text("https://j-lyric.net" + href)
        except Exception:
            continue
        body = re.search(r'<p id="Lyric">(.*?)</p>', song, re.S)
        lyrics = _html_to_text(body.group(1)) if body else ""
        if lyrics:
            return lyrics
    return ""


def fetch_lyrics(artist, title, duration=0):
    """Lyrics string for a track, '' if nothing found. LRCLIB first (exact /api/get
    then /api/search), then the NetEase/Kugou/J-Lyric fallbacks."""
    if not (artist or title):
        return ""
    params = {"artist_name": artist, "track_name": title}
    if duration:
        params["duration"] = int(duration)
    try:
        got = pick_lyrics(_get_json("https://lrclib.net/api/get?" + urllib.parse.urlencode(params)))
        if got:
            return got
    except Exception:
        pass
    query = " ".join(p for p in (artist, title) if p)
    try:
        rows = _get_json("https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": query}))
        for row in (rows if isinstance(rows, list) else [])[:5]:
            got = pick_lyrics(row)
            if got:
                return got
    except Exception:
        pass
    # Fallbacks: undocumented/scraped, each best-effort. J-Lyric searches a title field.
    for provider, arg in ((_netease_lyrics, query), (_kugou_lyrics, query), (_jlyric_lyrics, title)):
        try:
            got = provider(arg)
        except Exception:
            continue
        if got:
            return got
    return ""


def read_tags(path):
    """(artist, title, duration_seconds) from an mp3; blanks/0 on failure."""
    try:
        audio = mutagen.File(path, easy=True)
        artist = (audio.get("artist") or [""])[0]
        title = (audio.get("title") or [""])[0]
        dur = getattr(getattr(audio, "info", None), "length", 0) or 0
        return artist.strip(), title.strip(), dur
    except Exception:
        return "", "", 0


def sidecar_exists(path):
    base = os.path.splitext(path)[0]
    return os.path.exists(base + ".lrc") or os.path.exists(base + ".txt")


def write_sidecar(path, lyrics):
    base = os.path.splitext(path)[0]
    out = base + (".lrc" if is_synced(lyrics) else ".txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(lyrics.strip() + "\n")
    return out


def main():
    found = 0
    for folder in MP3_FOLDERS:
        if not os.path.isdir(folder):
            print(f"Folder missing, skipping: {folder}")
            continue
        print("Processing folder", folder)
        for name in os.listdir(folder):
            if not name.lower().endswith(".mp3"):
                continue
            path = os.path.join(folder, name)
            if sidecar_exists(path):
                continue
            artist, title, dur = read_tags(path)
            if not title:
                continue
            lyrics = fetch_lyrics(artist, title, dur)
            if lyrics:
                out = write_sidecar(path, lyrics)
                found += 1
                print(f"[+] {name} -> {os.path.basename(out)}")
            else:
                print(f"[ ] {name}: no lyrics")
            time.sleep(RATE_LIMIT_S)
    print(f"Done. Wrote {found} sidecars.")


if __name__ == "__main__":
    main()
