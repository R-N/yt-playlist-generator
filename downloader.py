import pandas as pd
from yt_dlp import YoutubeDL
from pathlib import Path
import os
import glob


id_file_name = "ids.txt"
download_folder = "downloads"
downloaded_log_file = "downloaded_ids.txt"
error_log_file = "error_ids.txt"

def main():
  # Load list of all video IDs
  all_ids = pd.read_csv('ids.txt', header=None)[0].dropna().unique().tolist()

  # Log file to track completed downloads
  log_file = Path(downloaded_log_file)
  error_file = Path(error_log_file)

  # Load completed IDs
  if log_file.exists():
      with open(log_file, 'r') as f:
          downloaded_ids = set(line.strip() for line in f)
  else:
      downloaded_ids = set()

  # Remaining videos
  to_download = [vid for vid in all_ids if vid not in downloaded_ids]

  # yt-dlp options
  ydl_opts = {
      'format': 'bestaudio/best',
      'extract_audio': True,
      'audio_format': 'mp3',
      'audio_quality': '160K',
      'outtmpl': 'downloads/%(uploader)s - %(title)s [%(id)s].%(ext)s',
      'quiet': False,
      'noplaylist': True,
      'overwrites': False,
      'max_filesize': None,  # Not limiting size directly
      'match_filter': lambda info: (
          "Skipping: too long" if info.get('duration', 0) > 1800 else None
      ),
      'sleep_interval': 5,
      'max_sleep_interval': 10,
      'postprocessors': [{
          'key': 'FFmpegExtractAudio',
          'preferredcodec': 'mp3',
          'preferredquality': '160',
      }],
  }

  Path(download_folder).mkdir(exist_ok=True)
  with YoutubeDL(ydl_opts) as ydl, open(log_file, 'a') as log, open(error_file, 'a') as error_log:
      for vid in to_download:
          url = f'https://www.youtube.com/watch?v={vid}'
          print(f"Downloading: {url}")
          try:
              ydl.download([url])
              log.write(vid + '\n')
              log.flush()
          except Exception as e:
              print(f"Failed: {url} — {e}")
              error_log.write(vid + '\n')
              error_log.flush()

if __name__ == '__main__':
    main()
