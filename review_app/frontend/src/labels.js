// Single source of status labels so Workspace and Library render identically and
// never drift. ("Labels" = these UI badges, distinct from mp3 file tags.) Each
// view maps a row into this shape and passes the result to LabelRow.vue.
//   hasLink    - a YouTube link exists (presence; used where health is unknown)
//   aliveLink  - link health-checked and reachable
//   deadLink   - link removed/private
//   localCount - number of linked mp3-folder files (may be >1 = duplicates)
//   downloaded - a file exists in the download folder (distinct from local file)
//   check      - review decision: 1 confirmed, 0 rejected, null unreviewed
export function buildLabels({ hasLink = false, aliveLink = false, deadLink = false, localCount = 0, downloaded = false, untracked = false, check = null } = {}) {
  const labels = []
  if (deadLink) labels.push({ key: 'dead', label: 'Unavailable', color: 'error', icon: 'mdi-youtube-off' })
  else if (aliveLink || hasLink) labels.push({ key: 'youtube', label: 'YouTube', color: 'error', icon: 'mdi-youtube' })
  if (localCount) labels.push({ key: 'local', label: localCount > 1 ? `${localCount} files` : 'Local file', color: 'primary', icon: 'mdi-music' })
  if (untracked) labels.push({ key: 'untracked', label: 'Untracked file', color: 'warning', icon: 'mdi-file-question-outline' })
  if (downloaded) labels.push({ key: 'downloaded', label: 'Downloaded', color: 'info', icon: 'mdi-download-circle' })
  if (check === 1) labels.push({ key: 'confirmed', label: 'Confirmed', color: 'success', icon: 'mdi-check-decagram' })
  else if (check === 0) labels.push({ key: 'rejected', label: 'Rejected', color: 'grey', icon: 'mdi-close-circle-outline' })
  return labels
}

// Attributes a tri-state filter can match on: label keys plus row-kind pseudo-labels.
export const FILTER_ATTRS = [
  { key: 'youtube', label: 'YouTube', icon: 'mdi-youtube' },
  { key: 'local', label: 'Local file', icon: 'mdi-music' },
  { key: 'downloaded', label: 'Downloaded', icon: 'mdi-download-circle' },
  { key: 'confirmed', label: 'Confirmed', icon: 'mdi-check-decagram' },
  { key: 'rejected', label: 'Rejected', icon: 'mdi-close-circle-outline' },
  { key: 'dead', label: 'Unavailable', icon: 'mdi-youtube-off' },
  { key: 'saved', label: 'Saved link', icon: 'mdi-bookmark-outline' },
  { key: 'untracked', label: 'Untracked file', icon: 'mdi-file-question-outline' },
]

// Tachiyomi-style tri-state: state 1 = must have, -1 = must not have, absent = ignore.
export function matchesLabelFilter(attrs, filter) {
  for (const key in filter) {
    const state = filter[key]
    if (state === 1 && !attrs.has(key)) return false
    if (state === -1 && attrs.has(key)) return false
  }
  return true
}
