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
