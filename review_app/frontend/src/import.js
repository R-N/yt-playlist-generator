const ID = /^[A-Za-z0-9_-]{11}$/

export function parsePaste(value) {
  const seen = new Set(); const parsed = []
  for (const raw of String(value).split(/\r?\n/).map((line) => line.trim()).filter(Boolean)) {
    let id = ID.test(raw) ? raw : null
    try {
      const url = new URL(raw.includes('://') ? raw : `https://${raw}`)
      if (url.hostname === 'youtu.be') id = url.pathname.slice(1).split('/')[0]
      if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(url.hostname) && url.pathname === '/watch') id = url.searchParams.get('v')
    } catch { /* invalid input */ }
    if (!id || !ID.test(id)) parsed.push({ input: raw, status: 'invalid' })
    else if (seen.has(id)) parsed.push({ input: raw, youtube_id: id, status: 'duplicate' })
    else { seen.add(id); parsed.push({ input: raw, youtube_id: id, status: 'valid' }) }
  }
  return parsed
}
