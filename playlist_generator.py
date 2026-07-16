#!/usr/bin/env python3
# playlist_generator.py - Creates YouTube playlist URLs from a list of video URLs/IDs.

import re

limit = 50
url_file_name = "urls.txt"
playlist_file_name = "playlists.txt"

# Pull the 11-char video id out of the common YouTube URL forms; fall back to a bare id
# or (legacy) the last 11 chars of a naked watch/youtu.be URL.
_ID_IN_URL = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_ids(lines):
    """Video ids from an iterable of URL/id lines, skipping blanks."""
    ids = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = _ID_IN_URL.search(line)
        if m:
            ids.append(m.group(1))
        elif _BARE_ID.match(line):
            ids.append(line)
        elif len(line) >= 11:
            ids.append(line[-11:])          # legacy: naked watch?v=ID / youtu.be/ID URL
    return ids


def build_playlists(ids, limit=limit):
    """watch_videos playlist URLs, ids chunked in groups of `limit`. Includes the final
    partial chunk (the old len(ids)//limit dropped it) and returns [] for no ids."""
    return [
        "https://www.youtube.com/watch_videos?video_ids=" + ",".join(ids[i:i + limit])
        for i in range(0, len(ids), limit)
    ]


def main():
    with open(url_file_name) as f:
        ids = extract_ids(f.readlines())
    playlists = build_playlists(ids, limit)
    print("Playlist created at:")
    for playlist in playlists:
        print(playlist)
    with open(playlist_file_name, "w") as f:
        f.writelines(f"{line}\n" for line in playlists)


if __name__ == "__main__":
    main()
