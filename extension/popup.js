const $ = (s) => document.querySelector(s);

function log(m) {
  const el = $('#log');
  el.textContent = m + '\n' + el.textContent;
}

function parseIds() {
  return $('#ids').value
    .split(/\s+/)
    .map((s) => s.trim())
    .filter((s) => /^[a-zA-Z0-9_-]{11}$/.test(s));
}

// Pull the harvested id list from the local Music Toolkit app (ids.txt).
async function loadFromApp() {
  try {
    const r = await fetch('http://localhost:8000/api/likes/queue');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    $('#ids').value = j.ids.join('\n');
    log(`loaded ${j.ids.length} ids from app`);
  } catch (e) {
    log('load failed (is the app running on :8000?) ' + e);
  }
}

async function activeYtTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/www\.youtube\.com\//.test(tab.url || '')) {
    throw new Error('open a logged-in youtube.com tab first');
  }
  return tab;
}

$('#load').onclick = loadFromApp;

$('#start').onclick = async () => {
  const ids = parseIds();
  if (!ids.length) { log('no valid 11-char ids'); return; }
  const delayMs = Math.max(2000, (Number($('#delay').value) || 4) * 1000);
  const mins = Math.round((ids.length * delayMs) / 1000 / 60);
  if (!confirm(`Like ${ids.length} videos on your account? ~${mins} min at ${delayMs / 1000}s each.`)) return;
  try {
    const tab = await activeYtTab();
    await chrome.tabs.sendMessage(tab.id, { type: 'startLikes', ids, delayMs });
    log(`started: ${ids.length} ids @ ${delayMs / 1000}s`);
  } catch (e) {
    log('error: ' + e.message);
  }
};

$('#stop').onclick = async () => {
  try {
    const tab = await activeYtTab();
    await chrome.tabs.sendMessage(tab.id, { type: 'stopLikes' });
    log('stopping after current...');
  } catch (e) {
    log(e.message);
  }
};

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== 'likeProgress') return;
  if (msg.done) {
    log(`DONE ok=${msg.ok} fail=${msg.fail}`
      + (msg.stopped ? ' (stopped)' : '')
      + (msg.error ? ' ' + msg.error : ''));
  } else {
    log(`${msg.i}/${msg.total} ${msg.id} ${msg.status}`);
  }
});
