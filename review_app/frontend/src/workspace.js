export function chunkItems(items, size = 50) {
  const batches = []
  for (let i = 0; i < items.length; i += size) batches.push(items.slice(i, i + size))
  return batches
}

export function selectedItems(items, selected) {
  const ids = new Set(selected)
  return items.filter((item) => ids.has(item.id))
}

// Significant (>2 char) alphanumeric tokens; drops a file extension.
export function nameTokens(text) {
  return (text || '').toLowerCase().replace(/\.[a-z0-9]+$/, '').split(/[^a-z0-9]+/).filter((token) => token.length > 2)
}

// Discovery-only overlap score between a video title and a file basename.
// ponytail: naive token-overlap heuristic; swap for tag/fingerprint match if it misfires.
export function nameMatchScore(title, basename) {
  const wanted = new Set(nameTokens(title))
  const have = nameTokens(basename)
  if (!wanted.size || !have.length) return 0
  return have.filter((token) => wanted.has(token)).length
}

export function fileDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function formatBytes(value) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

export const ACTIVE_RUN_STATUSES = ['queued', 'running', 'finalizing']

export function activeRun(runs) {
  return (Array.isArray(runs) ? runs : []).find((run) => ACTIVE_RUN_STATUSES.includes(run?.status)) || null
}

export function preferredRun(runs) {
  const list = Array.isArray(runs) ? runs : []
  return activeRun(list) || list[0] || null
}

export function batchSnapshotIds(batches) {
  return (Array.isArray(batches) ? batches : []).flatMap((batch) => batch.item_ids || [])
}

export function folderLines(text) {
  return String(text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
}

export function deletionMessage(result) {
  if (result?.status === 'partial') return `Deletion partial: ${result.deleted || 0} deleted, ${result.rejected || 0} rejected. Audit reloaded.`
  return `Deletion finished: ${result?.deleted?.length || 0} files deleted.`
}

export function duplicateMessage(source, count) {
  if (!count) return ''
  return `${source} omitted ${count} duplicate item${count === 1 ? '' : 's'}.`
}

export function skippedDuplicateCount(response) {
  return Array.isArray(response?.skipped_duplicate_item_ids)
    ? response.skipped_duplicate_item_ids.length
    : 0
}

// --- YouTube item metadata / health (fetched by /api/workspace/enrich) ------
export function itemMeta(item) {
  if (!item?.metadata_json) return {}
  try {
    const parsed = JSON.parse(item.metadata_json)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch { return {} }
}

// Confirmed gone/locked only; 'unknown' (network blip) is NOT dead so we never
// false-strike a live link.
export function isDead(item) {
  const health = itemMeta(item).health
  return health === 'dead' || health === 'private'
}
export function isEnriched(item) { return 'health' in itemMeta(item) }

export function healthLabel(item) {
  return { dead: 'Unavailable', private: 'Private', unknown: 'Unverified', ok: '' }[itemMeta(item).health] || ''
}

const baseName = (path) => (path ? path.split(/[\\/]/).pop() : null)

// Title only. Order: item column -> fetched metadata -> linked track ->
// file tag title (link-less/file-only items) -> file name.
export function itemName(item) {
  const meta = itemMeta(item)
  const title = item?.title || meta.title || item?.track_title || item?.tag_title || item?.track_filename || baseName(item?.relative_path)
  if (title) return title
  return isDead(item) ? 'Unavailable video' : (item?.youtube_id || 'YouTube video')
}

// Author/channel line, same fallback order (incl. file tag artist).
export function itemAuthor(item) {
  const meta = itemMeta(item)
  return item?.channel || meta.channel || item?.track_artist || item?.tag_artist || ''
}

// Combined "Author – Title" — kept for aria labels and discovery search names.
export function itemTitle(item) {
  const line = [itemAuthor(item), itemName(item)].filter(Boolean).join(' – ')
  return line || itemName(item)
}

export function formatViews(n) {
  if (n == null) return ''
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B views'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M views'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K views'
  return `${n} views`
}
export function formatDuration(s) {
  const n = Math.round(s || 0)
  if (!n) return ''
  const h = Math.floor(n / 3600); const m = Math.floor((n % 3600) / 60); const sec = n % 60
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(sec).padStart(2, '0')}`
}
export function formatUploadDate(d) {
  return d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : ''
}
