# Music Toolkit — YT Liker

Chrome extension that likes YouTube videos on your own logged-in account. It
is compatibility tooling, not primary app navigation. The app queue endpoint
(`GET /api/likes/queue`) serves legacy `ids.txt`; paste IDs also works.

## How it works

On `youtube.com`, content script calls YouTube internal
`youtubei/v1/like/like`. Browser cookies provide session auth; the extension
computes `SAPISIDHASH`. Likes are throttled (default 4s plus jitter) and Stop
halts after current item.

## Install

1. Open `chrome://extensions` and enable Developer mode.
2. Choose **Load unpacked** and select this `extension/` folder.
3. Pin extension if useful.

## Use

1. Run app on `:8000`, or prepare a list of 11-character YouTube IDs.
2. Open logged-in `youtube.com` tab.
3. Choose **Load from app** for compatibility queue, or paste IDs.
4. Set delay, click **Like all**, confirm. Stop when needed.

Workspace imports and persists current YouTube work; extension queue remains
legacy compatibility surface only.

## Cautions

- Bulk liking can trigger spam protection. Test 5–10 IDs first and keep delay
  several seconds.
- This automates account writes; use at own risk.
- YouTube internal API key, endpoint, and `SAPISID` cookie can change. Use a
  normal watch/home page so API key is available.
