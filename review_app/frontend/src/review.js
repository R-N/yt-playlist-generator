// Pure, UI-free logic for the review screen. Unit-tested in review.test.js.

// Map a keyboard key to a review action (null = ignore).
export function keyToAction(key) {
  if (key === 'a' || key === 'ArrowRight') return 'approve'
  if (key === 'r' || key === 'ArrowLeft') return 'reject'
  if (key === 'ArrowUp') return 'back'
  return null
}

// Format a numeric field for display; blank-ish -> en dash.
export function fmt(n, d = 0) {
  return n == null || n === '' ? '–' : Number(n).toFixed(d)
}

// Advance within the current batch. reload=true means the batch is exhausted
// and the caller should fetch the next page.
export function advanceIndex(idx, length) {
  if (idx < length - 1) return { idx: idx + 1, reload: false }
  return { idx, reload: true }
}

export function prevIndex(idx) {
  return idx > 0 ? idx - 1 : 0
}

// YouTube IFrame embed URL for a candidate id.
export function youtubeEmbed(id) {
  return id ? `https://www.youtube.com/embed/${id}` : null
}

// Vuetify color for an AcoustID/MusicBrainz cross-check confidence.
export function confidenceColor(conf) {
  return { strong: 'green', weak: 'amber', none: 'grey' }[conf] || 'grey'
}

// Library track states (server-derived in main._track_state) -> display meta.
export const STATE_META = {
  confirmed:  { label: 'Confirmed',  color: 'success', icon: 'mdi-check-decagram' },
  unreviewed: { label: 'Unreviewed', color: 'grey',    icon: 'mdi-help-circle-outline' },
  rejected:   { label: 'Rejected',   color: 'error',   icon: 'mdi-close-circle-outline' },
  file_only:  { label: 'File only',  color: 'info',    icon: 'mdi-file-music-outline' },
  link_only:  { label: 'Link only',  color: 'warning', icon: 'mdi-link-variant' },
  new:        { label: 'New',        color: 'purple',  icon: 'mdi-new-box' },
}

export function stateMeta(state) {
  return STATE_META[state] || { label: state || '—', color: 'grey', icon: 'mdi-music' }
}

// Filter library rows by state ('all' = any) and a free-text query over the
// human-visible fields. Pure so it's unit-tested in review.test.js.
export function filterLibrary(rows, state = 'all', query = '') {
  const q = query.trim().toLowerCase()
  return rows.filter((r) => {
    if (state !== 'all' && r.state !== state) return false
    if (!q) return true
    return [r.artist, r.title, r.filename, r.yt_title, r.yt_channel]
      .some((v) => v && String(v).toLowerCase().includes(q))
  })
}
