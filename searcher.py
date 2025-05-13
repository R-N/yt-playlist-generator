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

YDL_OPTS = {
  # "cookiesfrombrowser": ("brave", "Profile 1"),
  #"cookiefile": "cookies.txt",
  'sleep_interval': 5,
  'max_sleep_interval': 10,
  #"ignoreerrors": True,
}

CONFIGS = [
  {
    "folder": "E:/Music/downloads",
    "file": "mp3_youtube_matches_0.csv"
  },
  {
    "folder": "E:/Music/My Music",
    "file": "mp3_youtube_matches_1.csv"
  },
  {
    "folder": "E:/Music/My Music Out 2",
    "file": "mp3_youtube_matches_2.csv"
  },
]

ACOUSTID_API_KEY = None
index = 0
MP3_FOLDER = CONFIGS[index]["folder"]
OUTPUT_FILE = CONFIGS[index]["file"]
SIGN_IN_FILE = "sign_in.txt"
BATCH_SIZE = 1
lock = threading.Lock()
kks = pykakasi.kakasi()

if ACOUSTID_API_KEY:
    import acoustid


def romanize_japanese(text):
    result = kks.convert(text)
    return " ".join([item['hepburn'] for item in result])

def get_metadata(mp3_path, separators=['-', '–']):
    try:
        #raise Exception("Skipping metadata")
        audio = mutagen.File(mp3_path, easy=True)
        artist = audio.get("artist", [""])[0]
        title = audio.get("title", [""])[0]
        
        return artist.strip(), title.strip()
    except Exception as e:
        print(f"Error extracting metadata for {mp3_path}: {e}")
        print(f"Guessing metadata from file name")
        filename = os.path.splitext(mp3_path)[0]
        
        for sep in separators:
            spaced_sep = f" {sep} "
            if spaced_sep in filename:
                artist, title = filename.split(spaced_sep, 1)
                return artist.strip(), title.strip()
            if sep in filename:
                artist, title = filename.split(sep, 1)
                return artist.strip(), title.strip()
        print(f"Failed guessing metadata")
        return "", ""

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
    artist = romanize_japanese(artist).lower()
    query_title = romanize_japanese(query_title).lower()
    score = 0

    # 1. Topic channel (highest priority)
    if "- topic" in channel:
        score += 25
    if "release" in channel:
        score += 25
    if "official" in channel:
        score += 25
    if "vevo" in channel:
        score += 25

    if "official" in title:
        score += 25
    if "audio" in title:
        score += 10
    if "lyrics" in title:
        score += 10

    # 2. Channel similar to artist name (medium priority)
    if artist:
        if artist in channel:
            score += 100
        else:
            sim = partial_ratio(artist, channel)
            score += int(sim*2) - 100

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
            sim_title = partial_ratio(query_title, title)
            score += int(sim_title*2) - 100

    if max_views and views:
        score += int(100*views/max_views)

    # 5. Penalize live
    if 'live' in title and 'live' not in query_title:
        score -= 100
    if 'remix' in title and 'remix' not in query_title:
        score -= 100
    if ('nightcore' in title or 'nightcore' in channel) and ('nightcore' not in query_title or 'nightcore' not in artist):
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
        if ext in preferred_formats:
            format_rank = preferred_formats.index(ext)
            score += (len(preferred_formats) - format_rank) * 10
        elif codec in preferred_formats:
            format_rank = preferred_formats.index(codec)
            score += (len(preferred_formats) - format_rank) * 10
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

    query = re.sub(r'\s+', ' ', query).strip()

    query2 = remove_symbols(query)
    if not query2:
        return query
    if title and partial_ratio(title, query2) < 50:
        return query
    return query2

def remove_symbols(query):
    query = re.sub(r'[「」（）【】《》✧～『』┃║→⚠️🎃♯×📢🎀💌☔☀☽☪🌠♠🌟♥▼▶︎●◆／\–\"\(\)\[\]\#\.\~\-\/\&\,\_\:]', ' ', query).strip() 
    query = re.sub(r'\s+', ' ', query).strip()
    return query

def extract_metadata(info, artist="", title="", max_views=1):
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
        "audio_format": best_audio.get("ext") if best_audio else None,
        "audio_codec": best_audio.get("acodec") if best_audio else None,
        "audio_bitrate": best_audio.get("abr") if best_audio else None,
        "score": entry_score,
    }
    

def search(ydl, query):
    #return ydl.extract_info(query, download=False)
    try:
        return ydl.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e).lower()
        if "sign in to confirm your age" in error_message:
            print(f"Age-restricted video (login required): {url}")
            with open(SIGN_IN_FILE, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
        if "sign in if you've been granted access" in error_message:
            print(f"Access-restricted video (login required): {url}")
            with open(SIGN_IN_FILE, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
        else:
            raise

def fetch_full_metadata(video_id, artist="", title=""):
    try:
        ydl_opts = {
            **YDL_OPTS,
            'quiet': True,
            'skip_download': True,
            'extract_flat': False,
            'default_search': 'ytsearch',
            'max_search_results': 1,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = search(ydl, video_id)
            return extract_metadata(info, artist, title)
    except Exception as e:
        print(f"Metadata fetch error for {video_id}: {e}")
        traceback.print_exc()
        return None

def get_entry_id(entry, keys=['id', 'yt_id', 'url', 'yt_url']):
    keys = [k for k in keys if k in entry]
    return entry[keys[0]]

def search_youtube_with_audio_info(query, artist="", title=""):
    try:
        n_search = 3
        n_metadata = 3
        ydl_opts = {
            **YDL_OPTS,
            'quiet': True,
            'skip_download': True,
            'extract_flat': False,
            'default_search': 'ytsearch' + str(n_search),
            'max_search_results': n_search,
        }
        video_id = extract_video_id_from_filename(query)
        if video_id:
            print("Using id:", video_id, "extracted from:", query)
            query = video_id
            ydl_opts['extract_flat'] = False
            return fetch_full_metadata(video_id)
        else:
            query = clean_query(query, title)
        # Step 1: Flat search (quick metadata only)

        print("Query:", query)

        entries = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = search(ydl, query)
            if result:
                entries = result.get("entries", [result])
        entries = [e for e in entries if e]

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
        return set(df["filename"].values)
    return set()

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
    processed_files = load_processed_files()
    threads = []
    for i, file in enumerate(os.listdir(MP3_FOLDER)):
        if file.lower().endswith(".mp3") and file not in processed_files:
            path = os.path.join(MP3_FOLDER, file)
            print(f"[{i+1}] Processing: {file}")
            artist, title = get_metadata(path)
            acoustid_id = get_acoustid(path)
            query = f"{artist} - {title}" if artist and title else os.path.splitext(file)[0]

            yt_info = search_youtube_with_audio_info(query, artist, title or query)

            result = {
                "filename": file,
                "artist": artist,
                "title": title,
                "acoustid_id": acoustid_id,
                "yt_query": None,
                "yt_id": None,
                "yt_channel": None,
                "yt_title": None,
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

main()
