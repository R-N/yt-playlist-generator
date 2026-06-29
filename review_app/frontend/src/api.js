// Thin fetch wrapper. All URLs relative -> Vite proxies /api to FastAPI.
async function jget(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export const api = {
  counts: () => jget('/api/counts'),
  rows: (status = 'unreviewed', limit = 200, offset = 0) =>
    jget(`/api/rows?status=${status}&limit=${limit}&offset=${offset}`),
  decide: (track_id, decision) => jpost('/api/decision', { track_id, decision }),
  export: () => jpost('/api/export'),
  audioUrl: (id) => `/api/audio/${id}`,

  // settings (.env secrets)
  getSettings: () => jget('/api/settings'),
  saveSettings: (body) => jpost('/api/settings', body),

  // discord harvest
  discordFetch: (body) => jpost('/api/discord/fetch', body),

  // pipeline scripts (background jobs)
  scripts: () => jget('/api/scripts'),
  scriptState: (name, tail) =>
    jget(`/api/scripts/${name}` + (tail ? `?tail=${tail}` : '')),
  scriptRun: (name, args = []) => jpost(`/api/scripts/${name}/run`, { args }),
  scriptStop: (name) => jpost(`/api/scripts/${name}/stop`),
}
