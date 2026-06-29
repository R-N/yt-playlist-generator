"""
Discord channel -> YouTube IDs, for the Discord tab.

Reuses the repo's standalone scripts directly (this is the "integrate the
scattered scripts into the app" path): discord_fetch.fetch_channel pulls the
messages via the bot REST API, discord_extractor.extract_youtube_ids pulls the
11-char video ids out of each message's text + embeds. The result is written to
the same pipeline files the downloader/playlist flow reads (ids.txt, urls.txt,
playlists.txt) and also returned to the UI.
"""
import os
import sys

from config import REPO_ROOT
import settings

# Make the repo-root scripts importable (they live one level up from review_app).
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import discord_fetch        # noqa: E402  (path set above)
import discord_extractor    # noqa: E402

CHUNK = 50      # video ids per watch_videos playlist url


def _message_text(msg):
    parts = [msg.get("content", "")]
    for emb in msg.get("embeds", []):
        parts.append(emb.get("url", "") or "")
        parts.append(emb.get("description", "") or "")
    return "\n".join(p for p in parts if p)


def fetch_and_extract(channel_id, token=None, author=None, write_files=True):
    token = token or settings.get("DISCORD_BOT_TOKEN")
    if not token:
        raise ValueError("no Discord bot token set — add one on the Settings tab")
    if not channel_id:
        raise ValueError("channel id is required")

    raw = discord_fetch.fetch_channel(str(channel_id), token)
    messages = discord_fetch.normalize(raw)

    seen, ids = set(), []
    for msg in messages:
        if author:
            name = msg["author"].get("nickname") or msg["author"].get("name")
            if name != author:
                continue
        for vid in discord_extractor.extract_youtube_ids(_message_text(msg)):
            if vid not in seen:
                seen.add(vid)
                ids.append(vid)

    urls = [f"https://www.youtube.com/watch?v={i}" for i in ids]
    playlists = [
        "https://www.youtube.com/watch_videos?video_ids=" + ",".join(ids[i:i + CHUNK])
        for i in range(0, len(ids), CHUNK)
    ]

    written = _write_pipeline_files(ids, urls, playlists) if write_files else []
    return {
        "messages": len(messages),
        "count": len(ids),
        "ids": ids,
        "urls": urls,
        "playlists": playlists,
        "written": written,
    }


def _write_pipeline_files(ids, urls, playlists):
    """Atomic write of the three pipeline files at the repo root. ids.txt is the
    downloader's input, so the Discord harvest feeds straight into download."""
    outputs = {"ids.txt": ids, "urls.txt": urls, "playlists.txt": playlists}
    for name, lines in outputs.items():
        path = os.path.join(REPO_ROOT, name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        os.replace(tmp, path)
    return list(outputs)
