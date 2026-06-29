// Likes a list of YouTube videos using the logged-in session's internal
// ("InnerTube") API. Runs as a content script on www.youtube.com, so every
// request is same-origin and carries the user's auth cookies automatically.
// The popup sends the id list + delay; this loops, likes one at a time, and
// streams progress back. This is the user acting as themselves in their own
// browser — not a headless bot — but it still automates writes, so it throttles
// and can be stopped. YouTube internals can change and break this.

function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return m ? decodeURIComponent(m[1]) : null;
}

// YouTube authenticates internal calls with SAPISIDHASH = ts_sha1("ts SAPISID origin").
async function sapisidHash() {
  const sapisid = getCookie('SAPISID') || getCookie('__Secure-3PAPISID');
  if (!sapisid) throw new Error('not logged in (no SAPISID cookie)');
  const origin = 'https://www.youtube.com';
  const ts = Math.floor(Date.now() / 1000);
  const buf = await crypto.subtle.digest(
    'SHA-1', new TextEncoder().encode(`${ts} ${sapisid} ${origin}`)
  );
  const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `SAPISIDHASH ${ts}_${hex}`;
}

// API key + client version live in the page's ytcfg; scrape them from the HTML
// (content scripts run isolated and can't read window.ytcfg directly).
function ytConfig() {
  const html = document.documentElement.innerHTML;
  const key = html.match(/"INNERTUBE_API_KEY":"([^"]+)"/);
  const ver = html.match(/"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"/)
    || html.match(/"clientVersion":"([^"]+)"/);
  if (!key) throw new Error('could not read INNERTUBE_API_KEY — open a normal youtube.com page');
  return { apiKey: key[1], clientVersion: ver ? ver[1] : '2.20240101.00.00' };
}

async function likeOne(videoId, cfg) {
  const res = await fetch(
    `https://www.youtube.com/youtubei/v1/like/like?key=${cfg.apiKey}&prettyPrint=false`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': await sapisidHash(),
        'X-Origin': 'https://www.youtube.com',
        'X-Goog-AuthUser': '0',
      },
      body: JSON.stringify({
        context: { client: { clientName: 'WEB', clientVersion: cfg.clientVersion } },
        target: { videoId },
      }),
    }
  );
  if (!res.ok) throw new Error('HTTP ' + res.status);
}

let stopFlag = false;

function report(p) {
  chrome.runtime.sendMessage({ type: 'likeProgress', ...p });
}

async function run(ids, delayMs) {
  let cfg;
  try {
    cfg = ytConfig();
  } catch (e) {
    report({ done: true, error: String(e) });
    return;
  }
  let ok = 0, fail = 0;
  for (let i = 0; i < ids.length; i++) {
    if (stopFlag) {
      report({ done: true, stopped: true, ok, fail });
      return;
    }
    const id = ids[i];
    try {
      await likeOne(id, cfg);
      ok++;
      report({ i: i + 1, total: ids.length, id, status: 'liked', ok, fail });
    } catch (e) {
      fail++;
      report({ i: i + 1, total: ids.length, id, status: 'error: ' + e.message, ok, fail });
    }
    // throttle hard: base delay + up to 40% jitter so it doesn't look mechanical
    await new Promise((r) => setTimeout(r, delayMs + delayMs * 0.4 * Math.random()));
  }
  report({ done: true, ok, fail });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'stopLikes') {
    stopFlag = true;
    sendResponse({ ok: true });
  } else if (msg.type === 'startLikes') {
    stopFlag = false;
    run(msg.ids, msg.delayMs);
    sendResponse({ ok: true });
  }
});
