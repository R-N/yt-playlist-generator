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
}
