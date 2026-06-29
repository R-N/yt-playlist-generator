import os
import time
import random
import mutagen
import pandas as pd
import re
import traceback

ACOUSTID_API_KEY = None
MP3_FOLDERS = [
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
]
PROCESSED_FILE = "matches.csv"
OUTPUT_FILE = "untracked.txt"
SIGN_IN_FILE = "sign_in.txt"

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
        passes = df["check"].notna() & ((df["check"] == 1) | (df["check"] == True) | (df["check"] == "True") | (df["check"] == "TRUE"))
        passes = passes.to_dict()
        return passes
    return {}

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
    passes = load_processed_files()
    threads = []
    for MP3_FOLDER in MP3_FOLDERS:
        print("Processing folder", MP3_FOLDER)
        for i, file in enumerate(os.listdir(MP3_FOLDER)):
            if file.lower().endswith(".mp3"):
                if file not in passes or not passes[file]:
                    results.append(file)

    results = pd.Series(results)
    results.to_csv(OUTPUT_FILE, index=False, header=False)

if __name__ == "__main__":
    main()
