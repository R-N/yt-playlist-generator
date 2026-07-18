import os
import time
import random
import mutagen
import yt_dlp
import pandas as pd
from rapidfuzz.fuzz import partial_ratio
import threading
import re
import traceback
from concurrent.futures import ThreadPoolExecutor
import pykakasi
import unicodedata

from folder_config import resolve_mp3_folders

YDL_OPTS = {
  # "cookiesfrombrowser": ("brave", "Profile 1"),
  # "cookiefile": "cookies.txt",
  'sleep_interval': 5,
  'max_sleep_interval': 10,
  #"ignoreerrors": True,
}

ACOUSTID_API_KEY = None
MP3_FOLDERS = resolve_mp3_folders([
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
])
OUTPUT_FILE = "matches.csv"
SIGN_IN_FILE = "sign_in.txt"
BATCH_SIZE = 1
n_search = 5
n_metadata = 5
lock = threading.Lock()
kks = pykakasi.kakasi()
no_search = False
sign_in_only = True
no_mhtml = False
fix_mhtml = False
no_id = False

if ACOUSTID_API_KEY:
    import acoustid

def is_valid_youtube_id(s):
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]{11}", s))

def penalized_partial_ratio(a: str, b: str, penalty_strength: float = 0.1) -> float:
    a, b = a.lower(), b.lower()
    base_score = partial_ratio(a, b)
    length_difference = abs(len(a) - len(b))
    max_length = max(len(a), len(b))
    if not max_length:
        return base_score
    length_penalty = 1 - penalty_strength * (length_difference / max_length)
    return base_score * length_penalty

def romanize_japanese(text):
    result = kks.convert(text)
    return " ".join([item['hepburn'] for item in result])

def get_metadata(mp3_path, separators=['-', '–']):
    try:
        #raise Exception("Skipping metadata")
        audio = mutagen.File(mp3_path, easy=True)
        artist = audio.get("artist", [""])[0]
        title = audio.get("title", [""])[0]
        composer = audio.get("composer", [""])[0]
        if not is_valid_youtube_id(composer):
            composer = ""
        
        return artist.strip(), title.strip(), composer.strip()
    except Exception as e:
        print(f"Error extracting metadata for {mp3_path}: {e}")
        print(f"Guessing metadata from file name")
        # basename only -- the old code split the full path, so a folder like "My-Music"
        # corrupted the guess. parse_title also drops [ytid]/bracket decorations.
        stem = os.path.splitext(os.path.basename(mp3_path))[0]
        artist, title = parse_title(stem)
        if artist or title:
            return artist.strip(), title.strip(), ""
        print(f"Failed guessing metadata")
        return "", "", ""

def get_acoustid(mp3_path):
    if not ACOUSTID_API_KEY:
        return None
    try:
        duration, fingerprint = acoustid.fingerprint_file(mp3_path)
        response = acoustid.lookup(ACOUSTID_API_KEY, fingerprint, duration)
        for score, rid, title, artist in acoustid.parse_lookup_result(response):
            return rid
    except:
        return None

def score(entry, artist, query_title="", max_views=1, preferred_formats=["opus", "ogg", "aac", "m4a", "mp3"]):
    views = entry.get("view_count", 1)
    channel = romanize_japanese(entry.get("uploader", "")).lower()
    uploader_id = entry.get("uploader_id", "")
    title = romanize_japanese(entry.get("title", "")).lower()
    artist = clean_artist(romanize_japanese(artist))
    query_title = romanize_japanese(query_title).lower()
    score = 0

    # 1. Topic channel (highest priority)
    channel_rewards = {
        '- topic': 25, 
        'release': 50, 
        'official': 25, 
        'vevo': 25, 
        'tv': 10,
        'ch.': 5,
    }
    for term, reward in channel_rewards.items():
        if term in channel:
            score += reward

    title_rewards = {
        'official': 10, 
        'audio': 5, 
        'full': 5, 
        'lyrics': 0, 
        'visualizer': 0, 
    }
    for term, reward in title_rewards.items():
        if term in title:
            score += reward

    # 2. Channel similar to artist name (medium priority)
    if artist:
        artists = [artist]
        if " " in artist:
            artists = [
                *artists,
                " ".join(reversed(artist.split(" ")))
            ]
        artist_score = -200
        for artist in artists:
            temp_score = 0
            if artist in channel:
                temp_score += 100
            else:
                sim = max(penalized_partial_ratio(artist, channel), penalized_partial_ratio(artist, uploader_id))
                temp_score += int(sim*2) - 100
            if artist in title:
                temp_score += 20
            else:
                sim = penalized_partial_ratio(artist, title)
                temp_score += int(sim*0.4) - 40
            artist_score = max(artist_score, temp_score)
        score += artist_score

    # 3. Channel looks like official YouTube (UC ID)
    if uploader_id:
        if uploader_id.startswith("UC"):
            score += 25
        if uploader_id.startswith("@"):
            score += 25

    # 5. Title similarity with query (new)
    if query_title:
        if query_title in title:
            score += 100
        else:
            sim_title = penalized_partial_ratio(query_title, title)
            score += int(sim_title*2) - 100

    if max_views and views:
        max_views = max(max_views, views)
        score += int(5*(views/max_views))

    # 5. Penalize
    channel_penalties = {
        'lover': 50, 
        'fans': 50, 
        'lyric': 50, 
        'video': 50, 
        'fan': 25, 
        'love': 25, 
        'forever': 25, 
    }
    for term, penalty in channel_penalties.items():
        if term in channel and term not in artist:
            score -= penalty
    title_penalties = {
        'instrumental': 100, 
        'short': 100, 
        'tv size': 100, 
        'tiktok': 100, 
        'live': 100, 
        'teaser': 100, 
        'remix': 100,
        'acoustic': 100, 
        'accoustic': 100, 
        'hikigatari': 100, 
        'karaoke': 100,
        '8bit': 100,
        '8-bit': 100,
        '8 bit': 100,
        'tour': 100,
        'inst': 25, 
        'mix': 25,
        'tab': 25,
        'tv': 25,
    }
    for term, penalty in title_penalties.items():
        if term in title and term not in query_title:
            score -= penalty
    if 'the first take' in channel and 'the first take' not in artist and 'the first take' not in query_title:
        score -= 25
    if ('nightcore' in title or 'nightcore' in channel) and ('nightcore' not in query_title and 'nightcore' not in artist):
        score -= 100

    # 6. Audio quality / format preference
    best_audio = None
    for f in entry.get("formats", []):
        if f.get("vcodec") == "none" and isinstance(f.get("abr", None), (int, float)):
            if not best_audio or f["abr"] > best_audio.get("abr", 0):
                best_audio = f

    if best_audio:
        ext = best_audio.get("ext", "").lower()
        codec = best_audio.get("acodec", "").lower()
        n = len(preferred_formats)
        if ext in preferred_formats:
            format_rank = preferred_formats.index(ext)
            score += int((n - format_rank) * 10/n)
        elif codec in preferred_formats:
            format_rank = preferred_formats.index(codec)
            score += int((n - format_rank) * 10/n)
        else:
            score -= 25

    return score

def extract_video_id_from_filename(name):
    # Match YouTube video ID inside square brackets at the end
    match = re.search(r"\[([a-zA-Z0-9_-]{11})\]$", name)
    return match.group(1) if match else None

def clean_query(query, title=''):
    query = query.replace('-', ' ')
    query = query.strip().strip('#').strip()

    noise_words = ["y2meta", ".com", "y2mate", "savefrom", "256 kbps", "320 kbps", "192 kbps", "160 kbps", "128 kbps", "kbps"]
    for word in noise_words:
        query = query.replace(word, " ").strip()

    query = re.sub(r'\s+', ' ', query).strip()

    query2 = remove_symbols(query)
    if not query2:
        return query
    if title and partial_ratio(title, query2) < 50:
        return query
    return query2

def clean_artist(name):
    if (not name) or not isinstance(name, str):
        return name
    name = remove_symbols(name)
    name = name.lower()
    noise_words = ["official", "topic", "channel", "チャンネル", "chan'neru", "channeru", "chaneru", " ch.", " x ", "feat.", " の ", " & ", "vevo", "tv", "youtube", "virtual", "singer", "youtuber", "vtuber"]
    for word in noise_words:
        name = name.replace(word, " ").strip()
    return " ".join(name.split())

def remove_symbols(query):
    if (not query) or not isinstance(query, str):
        return query
    query = re.sub(r'[　「」（）【】《》✧～『』┃║→⚠️🎃♯×📢🎀💌☔☀☽☪🌠◇★♣♠🌟♥ㆁ。♪♫・♡▼▶︎●◆🌷／\、\–\|\"\(\)\[\]\*\#\.\~\-\/\&\,\_\:\|]', ' ', query).strip() 
    query = re.sub(r'\s+', ' ', query).strip()
    return query

# Offline "messy title -> (artist, title)" parser, ported from usb-ldac's ytmeta.py
# (which itself grew out of this file). Used as the filename-guess fallback in
# get_metadata() -- handles bracket decorations, JP 「」 title quoting, and noise words
# that a naive split('-') mangles.
_TITLE_NOISE = [
    "official music video", "official video", "official audio", "official mv",
    "official lyric video", "lyric video", "music video", "official", "full ver",
    "full version", "full", "mv", "m/v", "audio", "hd", "4k", "hq", "visualizer",
    "lyrics", "lyric", "color coded", "explicit",
]
_TITLE_SEPARATORS = [" - ", " – ", " — ", " ~ ", " / ", "「", "『"]
_TITLE_CLOSERS = "」』"

def _drop_bracketed(text):
    # Remove (...) [...] 【...】 groups (feat/official/MV decorations, trailing [ytid]).
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\s*[\(\[【][^\(\)\[\]【】]*[\)\]】]\s*", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_title(raw_title, channel=""):
    """Best-effort (artist, title) from a messy title, offline. Splits on a separator
    ("Artist - Title", "Artist「Title」") when present; else channel is the artist and the
    whole cleaned string is the title."""
    raw = _drop_bracketed(raw_title or "")
    artist, title = "", raw
    for sep in _TITLE_SEPARATORS:
        if sep in raw:
            left, right = raw.split(sep, 1)
            if sep in "「『":
                right = right.rstrip(_TITLE_CLOSERS)
            artist, title = left.strip(), right.strip()
            break
    if not artist:
        artist = channel or ""
    title = title.strip().strip(_TITLE_CLOSERS)
    title = re.sub(r"(?i)\b(" + "|".join(re.escape(w) for w in _TITLE_NOISE) + r")\b", " ", title)
    title = re.sub(r"\s+", " ", title).strip(" -–—/|")
    artist = re.sub(r"\s+", " ", artist).strip(" -–—/|")
    return artist, (title or raw.strip())

def extract_metadata(info, artist="", title="", max_views=1):
    try:
        best_audio = None
        for f in info.get("formats", []):
            if f.get("vcodec") == "none":  # Audio only
                abr = f.get("abr")
                if abr is not None and (best_audio is None or abr > best_audio.get("abr", 0)):
                    best_audio = f
        entry_score = score(info, artist, title, max_views)
        return {
            "yt_id": info.get("id"),
            "yt_title": info.get("title"),
            "yt_channel": info.get("uploader"),
            "yt_channel_id": info.get("uploader_id"),
            "yt_views": info.get("view_count"),
            "duration": info.get("duration"),
            "audio_format": best_audio.get("ext") if best_audio else None,
            "audio_codec": best_audio.get("acodec") if best_audio else None,
            "audio_bitrate": best_audio.get("abr") if best_audio else None,
            "score": entry_score,
        }
    except AttributeError as e:
        error_message = str(e).lower()
        if "nonetype" in error_message:
            print("Error info is None")
        else:
            raise
    

def search(ydl_opts, query, cookies=False):
    if sign_in_only:
        if query in sign_ins:
            cookies = True
        else:
            print("Skipping because not in sign in list")
            return None
    if cookies:
        ydl_opts = {
            **ydl_opts,
            "cookiefile": "cookies.txt",
        }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e).lower()
        if "sign in to confirm your age" in error_message:
            print(f"Age-restricted video (login required): {query}")
            with open(SIGN_IN_FILE, "a", encoding="utf-8") as f:
                f.write(f"{query}\n")
            if not cookies:
                return search(ydl_opts, query, cookies=True)
        elif "sign in if you've been granted access" in error_message:
            print(f"Private video: {query}")
        elif "video unavailable" in error_message:
            print(f"Video unavailable: {query}")
        else:
            raise
    except yt_dlp.utils.ExtractorError as e:
        error_message = str(e).lower()
        if "video unavailable" in error_message:
            print(f"Video unavailable: {query}")
        else:
            raise

def fetch_full_metadata(video_id, artist="", title=""):
    ydl_opts = {
        **YDL_OPTS,
        'quiet': True,
        'skip_download': True,
        'extract_flat': False,
        'default_search': 'ytsearch',
        'max_search_results': 1,
    }
    info = search(ydl_opts, video_id)
    return extract_metadata(info, artist, title)

def get_entry_id(entry, keys=['id', 'yt_id', 'url', 'yt_url']):
    keys = [k for k in keys if k in entry]
    return entry[keys[0]]

def search_youtube_with_audio_info(query, artist="", title="", composer="", no_search=False):
    try:
        ydl_opts = {
            **YDL_OPTS,
            'quiet': True,
            'skip_download': True,
            'extract_flat': False,
            'default_search': 'ytsearch' + str(n_search),
            'max_search_results': n_search,
        }
        video_ids = [composer, extract_video_id_from_filename(query)]
        if no_id:
            print("Skipping id")
        else:
            for video_id in video_ids:
                if video_id:
                    print("Using id:", video_id, "extracted from:", query)
                    result = fetch_full_metadata(video_id)
                    if result: 
                        if no_mhtml and (result['audio_format'] == 'mhtml' or not result['yt_views']):
                            pass
                        else:
                            return result
        if no_search:
            print("Skipping because no search")
            return None
        query = clean_query(query, title)
        # Step 1: Flat search (quick metadata only)

        print("Query:", query)

        entries = []
        result = search(ydl_opts, query)
        if result:
            entries = result.get("entries", [result])
        entries = [e for e in entries if e]
        if no_mhtml:
            entries = [e for e in entries if e and e['audio_format'] != 'mhtml' and e['yt_views']]

        if not entries:
            print(f"No entries found for query: {query}")
            return None

        # Step 2: Score and select top 3 candidates
        max_views = max(e["view_count"] or 1 for e in entries)
        entries = [extract_metadata(e, artist, title, max_views) for e in entries]
        entries = [e for e in entries if e]
        entries = sorted(entries, key=lambda e: e['score'], reverse=True)[:n_metadata]

        # Step 3: Concurrently fetch full metadata for top 3
        # video_urls = [get_entry_id(entry) for entry in entries]
        # with ThreadPoolExecutor(max_workers=n_metadata) as executor:
        #     entries = list(executor.map(lambda vid: fetch_full_metadata(vid, artist), video_urls))
        # entries = [e for e in entries if e]

        # Step 4: Choose the best scored result
        if not entries:
            print("No valid metadata fetched.")
            return None

        best = max(entries, key=lambda e: e["score"])

        best["yt_query"] = query

        print("Found:", best["score"], best["yt_channel"], "-", best["yt_title"])

        return best

    except Exception as e:
        print(f"Search error: {e}")
        traceback.print_exc()
        return None

# Check for already processed files in the output file
def load_processed_files():
    if os.path.exists(OUTPUT_FILE):
        df = pd.read_csv(OUTPUT_FILE)
        df = df[df['yt_id'].notna() & (df['yt_id'] != '')]
        df.to_csv(OUTPUT_FILE, index=False)
        df = df.sort_values("check", ascending=True).drop_duplicates(subset="filename", keep="last")
        df = df.set_index("filename")
        yt_ids = df["yt_id"].to_dict()
        formats = df["audio_format"].to_dict()
        passes = df["check"].notna() & ((df["check"] == 1) | (df["check"] == True) | (df["check"] == "True") | (df["check"] == "TRUE"))
        passes = passes.to_dict()
        return yt_ids, passes, formats
    return {}, {}, {}

def load_sign_in():
    if os.path.exists(SIGN_IN_FILE):
        with open(SIGN_IN_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f)
    return {}

sign_ins = load_sign_in()

# Function to save to CSV using threading
def save_to_csv(results):
    with lock:  # Ensure only one thread can write to the file at a time
        df = pd.DataFrame(results)
        if os.path.exists(OUTPUT_FILE):
            df.to_csv(OUTPUT_FILE, mode='a', index=False, header=False)  # Append without header
        else:
            df.to_csv(OUTPUT_FILE, mode='w', index=False)  # Write with header if file doesn't exist

# Process all MP3s
def main():
    results = []
    yt_ids, passes, formats = load_processed_files()
    threads = []
    for MP3_FOLDER in MP3_FOLDERS:
        print("Processing folder", MP3_FOLDER)
        for i, file in enumerate(os.listdir(MP3_FOLDER)):
            if file.lower().endswith(".mp3"):
                path = os.path.join(MP3_FOLDER, file)
                artist, title, composer = get_metadata(path)
                condition_0 = (file not in yt_ids) or (not passes[file]) or (formats[file] == 'mhtml')
                condition = condition_0 or (composer and composer != yt_ids[file])
                if file in passes and passes[file]:
                    continue
                if no_search:
                    condition = composer and (file not in yt_ids or composer != yt_ids[file]) and (file not in passes or not passes[file])
                if fix_mhtml:
                    condition = file in formats and formats[file] == 'mhtml'
                    if condition:
                        composer = yt_ids[file]
                if condition:
                    print(f"[{i+1}] Processing: {file}")
                    acoustid_id = get_acoustid(path)
                    query = f"{artist} - {title}" if artist and title else os.path.splitext(file)[0]

                    yt_info = search_youtube_with_audio_info(
                        query, 
                        artist, 
                        title or query, 
                        composer, 
                        no_search or fix_mhtml or (not condition_0 and not sign_in_only)
                    )

                    result = {
                        "filename": file,
                        "artist": artist,
                        "title": title,
                        "acoustid_id": acoustid_id,
                        "yt_query": None,
                        "yt_id": None,
                        "yt_channel": None,
                        "yt_title": None,
                        "duration": None,
                        "audio_format": None,
                        "audio_codec": None,
                        "audio_bitrate": None,
                        "score": 0,
                    }

                    if yt_info:
                        result = {
                            **result,
                            **yt_info,
                        }
                        
                    result = {
                        **result,
                        "duplicated": None,
                        "sim_artist": None,
                        "sim_artist_title": None,
                        "sim_title": None,
                        "sim_filename": None,
                        "pass": None,
                        "check": None
                    }

                    if not result["yt_id"]:
                        continue

                    results.append(result)

                    # Save results asynchronously after every BATCH_SIZE entries
                    if len(results) % BATCH_SIZE == 0:
                        thread = threading.Thread(target=save_to_csv, args=(list(results),))
                        thread.start()
                        threads.append(thread)  # Store the thread for later joining
                        results = []


    # Ensure remaining results are saved
    if results:
        thread = threading.Thread(target=save_to_csv, args=(results,))
        thread.start()
        threads.append(thread)

    # Wait for all threads to complete
    for thread in threads:
        thread.join()

    print("Done.")

if __name__ == "__main__":
    main()
