import pandas as pd
from yt_dlp import YoutubeDL
import yt_dlp
from pathlib import Path
import os
import glob


id_file_name = "ids.txt"
download_folder = "downloads"
downloaded_log_file = "downloaded_ids.txt"
error_log_file = "error_ids.txt"
SIGN_IN_FILE = "sign_in.txt"
sign_in_only = False
    
# yt-dlp options
YDL_OPTS = {
  # "cookiesfrombrowser": ("brave", "Profile 1"),
  # "cookiefile": "cookies.txt",
  'format': 'bestaudio/best',
  'extract_audio': True,
  'audio_format': 'opus',
  'audio_quality': '160K',
  'outtmpl': 'downloads/%(uploader)s - %(title)s [%(id)s].%(ext)s',
  'quiet': False,
#   'verbose': True,
  'noplaylist': True,
  'overwrites': False,
  'max_filesize': None,  # Not limiting size directly
  'match_filter': lambda info: (
      "Skipping: too long" if info.get('duration', 0) > 1800 else None
  ),
  'sleep_interval': 1,
  'max_sleep_interval': 3,
  'postprocessors': [
      {
          'key': 'FFmpegExtractAudio',
          'preferredcodec': 'opus',
          'preferredquality': '0',
      },
      {
          'key': 'FFmpegMetadata',
          'add_metadata': True,
      },
      { 
          'key': 'EmbedThumbnail',
          'already_have_thumbnail': False,
      },
  ],
  'writethumbnail': True,
  'embedthumbnail': True,
  'addmetadata': True,
}

def load_sign_in():
    if os.path.exists(SIGN_IN_FILE):
        with open(SIGN_IN_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f)
    return {}

sign_ins = load_sign_in()

def download(ydl_opts, url, cookies=False):
    if sign_in_only:
        if url in sign_ins:
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
        with YoutubeDL(ydl_opts) as ydl:
            return ydl.download([url]) or True
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e).lower()
        if "sign in to confirm your age" in error_message:
            print(f"Age-restricted video (login required): {url}")
            with open(SIGN_IN_FILE, "a", encoding="utf-8") as f:
                f.write(f"{url}\n")
            if not cookies:
                return search(ydl_opts, url, cookies=True)
        elif "sign in if you've been granted access" in error_message:
            print(f"Private video: {url}")
        elif "video unavailable" in error_message:
            print(f"Video unavailable: {url}")
        else:
            raise
    except yt_dlp.utils.ExtractorError as e:
        error_message = str(e).lower()
        if "video unavailable" in error_message:
            print(f"Video unavailable: {url}")
        else:
            raise

def main():
  # Load list of all video IDs
  all_ids = pd.read_csv('ids.txt', header=None)[0].dropna().unique().tolist()

  # Log file to track completed downloads
  log_file = Path(downloaded_log_file)
  error_file = Path(error_log_file)

  # Load completed IDs
  if log_file.exists():
      with open(log_file, "r", encoding="utf-8") as f:
          downloaded_ids = set(line.strip() for line in f)
  else:
      downloaded_ids = set()

  # Remaining videos
  to_download = [vid for vid in all_ids if vid not in downloaded_ids]

  Path(download_folder).mkdir(exist_ok=True)
  with open(log_file, 'a') as log, open(error_file, 'a') as error_log:
      for vid in to_download:
        ydl_opts = {
            **YDL_OPTS,
            "postprocessor_args": [
                '-metadata', f'URL=https://www.youtube.com/watch?v={vid}',
                '-metadata', f'composer={vid}',
                '-metadata', f'yt_id={vid}',
                '-ac', '2',
            ]
        }
        url = f'https://www.youtube.com/watch?v={vid}'
        print(f"Downloading: {url}")
        try:
            if download(ydl_opts, url):
                log.write(vid + '\n')
                log.flush()
        except Exception as e:
            print(f"Failed: {url} — {e}")
            error_log.write(vid + '\n')
            error_log.flush()

if __name__ == '__main__':
    main()
