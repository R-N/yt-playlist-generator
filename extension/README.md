# Music Toolkit — YT Liker (Chrome extension)

Likes a list of YouTube videos on **your own account**, using your already
logged-in browser session — no OAuth, no API key. Built to consume the video
ids the toolkit harvests (e.g. from the Discord tab → `ids.txt`) and like them
in bulk, throttled.

## How it works

A content script on `youtube.com` calls YouTube's internal
`youtubei/v1/like/like` endpoint. Because it runs in your logged-in tab the
request is same-origin and carries your auth cookies automatically; it computes
the `SAPISIDHASH` authorization header from your `SAPISID` cookie the same way
YouTube's own page JS does. This is *you*, in *your* browser — not a headless
bot on a server — which is the lowest-risk way to do this. It still automates
writes, so it throttles (default 4s + jitter between likes) and has a Stop
button.

## Install (unpacked)

1. `chrome://extensions` → enable **Developer mode** (top right).
2. **Load unpacked** → select this `extension/` folder.
3. Pin the extension if you like.

## Use

1. Run the toolkit app (`review_app`, on `:8000`) and harvest ids into `ids.txt`
   (Discord tab), or have a list of 11-char video ids ready.
2. Open a **logged-in** `youtube.com` tab.
3. Click the extension → **Load from app** (pulls `ids.txt` via
   `GET :8000/api/likes/queue`) or paste ids into the box.
4. Set the per-like delay, click **Like all**, confirm. Progress streams in the
   popup; **Stop** halts after the current item.

## ⚠️ Cautions

- **Bulk liking can trip YouTube's spam protection.** Keep the delay sane (don't
  go below a few seconds). On the first run, test with 5–10 ids and confirm they
  appear in your Liked playlist before scaling up.
- This automates writes to your account — your account, your risk.
- It depends on YouTube internals (`INNERTUBE_API_KEY`, the like endpoint, the
  `SAPISID` cookie). If YouTube changes those, likes start returning HTTP errors
  and the extension needs updating. Open a normal watch/home page (not an embed)
  so the api key is present to scrape.
