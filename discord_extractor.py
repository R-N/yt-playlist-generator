import json
import re
import sys
import pandas as pd

# Extracts YouTube video IDs from a Discord channel export and emits the same
# outputs as url_extractor.py (ids / urls / playlists). Built for the export
# produced by DiscordChatExporter (https://github.com/Tyrrrz/DiscordChatExporter)
# in either JSON or CSV mode, but any CSV with a text column works.

dump_file_name = "discord.json"   # .json or .csv export from DiscordChatExporter
id_file_name = "ids1.txt"
url_file_name = "urls.txt"
playlist_file_name = "playlists.txt"
author = None      # set to a username/nickname to keep only that author's links; None = everyone
limit = 50

# matches any youtube link form and captures the 11-char video id
YT_RE = re.compile(
    r'(?:youtube\.com/(?:watch\?(?:[^\s]*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)'
    r'([a-zA-Z0-9_-]{11})'
)

def extract_youtube_ids(text):
    if not isinstance(text, str):
        return []
    return YT_RE.findall(text)

def iter_messages(path):
    """Yield (author, content) pairs from a Discord export (JSON or CSV)."""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for msg in data.get("messages", []):
            name = (msg.get("author") or {}).get("nickname") \
                or (msg.get("author") or {}).get("name")
            # links can live in content or in embed urls
            parts = [msg.get("content", "")]
            for emb in msg.get("embeds", []):
                parts.append(emb.get("url", "") or "")
                parts.append(emb.get("description", "") or "")
            yield name, "\n".join(p for p in parts if p)
    else:
        df = pd.read_csv(path)
        author_col = next((c for c in df.columns if c.lower() == "author"), None)
        content_col = next(
            (c for c in df.columns if c.lower() in ("content", "message")), None
        )
        if content_col is None:
            raise ValueError(f"no content/message column in {path}; got {list(df.columns)}")
        for _, row in df.iterrows():
            name = row[author_col] if author_col else None
            yield name, row[content_col]

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else dump_file_name
    seen = set()
    ids = []
    for name, content in iter_messages(path):
        if author is not None and name != author:
            continue
        for vid in extract_youtube_ids(content):
            if vid not in seen:
                seen.add(vid)
                ids.append(vid)

    with open(id_file_name, "w") as f:
        f.writelines(f"{i}\n" for i in ids)
    with open(url_file_name, "w") as f:
        f.writelines(f"https://www.youtube.com/watch?v={i}\n" for i in ids)

    playlists = []
    for i in range(0, len(ids), limit):
        group = ids[i:i + limit]
        playlist = "https://www.youtube.com/watch_videos?video_ids=" + ",".join(group)
        print(playlist)
        playlists.append(playlist)
    with open(playlist_file_name, "w") as f:
        f.writelines(f"{line}\n" for line in playlists)

    print(f"{len(ids)} unique video ids -> {id_file_name}, {url_file_name}, {playlist_file_name}")

if __name__ == "__main__":
    main()
