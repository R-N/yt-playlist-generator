import json
import os
import sys
import time
import urllib.error
import urllib.request

# Fetches all messages from one Discord channel via the bot REST API and writes
# them to discord.json in the shape discord_extractor.py reads, so the pipeline
# is:  python discord_fetch.py <channel_id>  ->  python discord_extractor.py
#
# Setup (one time):
#   1. https://discord.com/developers/applications -> New Application -> Bot.
#   2. Enable the "Message Content Intent" toggle (Bot page) so content is sent.
#   3. Invite the bot to your server (OAuth2 URL, scope=bot, perm = Read Message
#      History + View Channel).
#   4. Copy the bot token, then:  set DISCORD_BOT_TOKEN=...   (env var, never hardcode)
#   5. In Discord enable Developer Mode, right-click the channel -> Copy ID.

API = "https://discord.com/api/v10"
out_file_name = "discord.json"
token_env = "DISCORD_BOT_TOKEN"
page_size = 100  # discord max per request

def _request(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bot {token}",
        "User-Agent": "yt-playlist-generator (https://github.com, 1.0)",
    })
    while True:
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited
                retry = float(e.headers.get("Retry-After", "1"))
                print(f"rate limited, sleeping {retry}s", file=sys.stderr)
                time.sleep(retry + 0.5)
                continue
            body = e.read().decode("utf-8", "replace")
            raise SystemExit(f"HTTP {e.code} from Discord: {body}")

def fetch_channel(channel_id, token):
    """Page backwards through a channel, oldest-last, returning all messages."""
    messages = []
    before = None
    while True:
        url = f"{API}/channels/{channel_id}/messages?limit={page_size}"
        if before:
            url += f"&before={before}"
        batch = _request(url, token)
        if not batch:
            break
        messages.extend(batch)
        before = batch[-1]["id"]
        print(f"fetched {len(messages)} messages...", file=sys.stderr)
        if len(batch) < page_size:
            break
        time.sleep(0.3)  # be polite under the rate limit
    return messages

def normalize(raw):
    """Discord returns newest-first; flip to chronological and keep only the
    fields discord_extractor.py uses (author, content, embeds). Shared by the
    CLI here and the review_app Discord service."""
    messages = []
    for m in reversed(raw):
        a = m.get("author") or {}
        messages.append({
            "author": {"name": a.get("username"), "nickname": a.get("global_name")},
            "content": m.get("content", ""),
            "embeds": [{"url": e.get("url", ""), "description": e.get("description", "")}
                       for e in m.get("embeds", [])],
        })
    return messages

def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python discord_fetch.py <channel_id> [out.json]")
    channel_id = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else out_file_name
    token = os.environ.get(token_env)
    if not token:
        raise SystemExit(f"set the {token_env} environment variable to your bot token")

    raw = fetch_channel(channel_id, token)
    messages = normalize(raw)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"messages": messages}, f, ensure_ascii=False, indent=2)
    print(f"{len(messages)} messages -> {out}")

if __name__ == "__main__":
    main()
