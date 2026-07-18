import os
import time
import random
import mutagen
import pandas as pd
import re
import traceback

from folder_config import resolve_mp3_folders

ACOUSTID_API_KEY = None
MP3_FOLDERS = resolve_mp3_folders([
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
])
PROCESSED_FILE = "matches.csv"

def is_valid_youtube_id(s):
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]{11}", s or ""))

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
        filename = os.path.splitext(mp3_path)[0]
        
        for sep in separators:
            spaced_sep = f" {sep} "
            if spaced_sep in filename:
                artist, title = filename.split(spaced_sep, 1)
                return artist.strip(), title.strip(), ""
            if sep in filename:
                artist, title = filename.split(sep, 1)
                return artist.strip(), title.strip(), ""
        print(f"Failed guessing metadata")
        return "", "", ""


def get_entry_id(entry, keys=['id', 'yt_id', 'url', 'yt_url']):
    keys = [k for k in keys if k in entry]
    return entry[keys[0]]

# Check for already processed files in the output file
def load_processed_files():
    if os.path.exists(PROCESSED_FILE):
        df = pd.read_csv(PROCESSED_FILE)
        df = df[df['yt_id'].notna() & (df['yt_id'] != '')]
        df = df.sort_values("check", ascending=True).drop_duplicates(subset="filename", keep="last")
        df = df.set_index("filename")
        passes = df["check"] == 1
        passes = passes.to_dict()
        return passes
    return {}

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
    passes = load_processed_files()
    candidate_counts = {}
    for folder in MP3_FOLDERS:
        for file in os.listdir(folder):
            if file.lower().endswith(".mp3"):
                key = os.path.normcase(file)
                candidate_counts[key] = candidate_counts.get(key, 0) + 1

    for MP3_FOLDER in MP3_FOLDERS:
        print("Processing folder", MP3_FOLDER)
        for i, file in enumerate(os.listdir(MP3_FOLDER)):
            if file.lower().endswith(".mp3"):
                if file in passes and passes[file]:
                    if candidate_counts[os.path.normcase(file)] > 1:
                        print(f"Skipping ambiguous approved filename: {file}")
                        continue
                    full_path = os.path.join(MP3_FOLDER, file)
                    print("Deleting:", full_path)
                    os.remove(full_path)

if __name__ == "__main__":
    main()
