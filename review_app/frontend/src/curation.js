// Shared list-view logic for the Workspace and Library screens, which are the
// same shape: a searchable, tri-state-label-filtered, paginated list of rows
// with the same context menus. Composables keep the reactive plumbing in one
// place; the MENU_ITEMS constants keep the menu definitions from drifting apart.
import { ref, computed, watch, onScopeDispose } from 'vue'
import { api } from './api'
import { reviewTrack, showInLibrary, showInWorkspace, invalidateData } from './nav'
import { ACTIVE_RUN_STATUSES, deletionMessage } from './workspace'

// Native OS file-picker → add absolute-path files to one destination. Shared by
// every screen (Workspace, Library, Import-untracked); each only passes its target
// and a reload. DRY: the picker + add flow lives once here.
//   target: 'library' | 'workspace' | 'untracked'
export function useFilePicker({ target, onDone = () => {}, onError = () => {} } = {}) {
  const picking = ref(false)
  async function pickFiles() {
    picking.value = true
    try {
      const { paths } = await api.pickFiles()
      if (!paths?.length) return
      const r = await api.addFilesByPath(paths, target)
      await onDone(r)
    } catch (e) { onError(e) }
    finally { picking.value = false }
  }
  return { picking, pickFiles }
}

// Tri-state label filter (see labels.js): tap cycles ignore -> must-have -> exclude.
export function useLabelFilter() {
  const labelFilter = ref({})
  const activeFilterCount = computed(() => Object.keys(labelFilter.value).length)
  function cycleFilter(key) {
    const state = labelFilter.value[key] || 0
    const next = { ...labelFilter.value }
    if (state === 0) next[key] = 1
    else if (state === 1) next[key] = -1
    else delete next[key]
    labelFilter.value = next
  }
  return { labelFilter, activeFilterCount, cycleFilter }
}

// Keyed multi-select shared by the list screens. `keys` (a ref/computed of the
// currently selectable ids/keys, optional) drives select-all: allSelected is true
// when every selectable key is picked; toggleAll unions or subtracts them,
// preserving any off-view selections. Omit keys for a plain toggle list (Import).
export function useSelection(keys = null) {
  const selected = ref([])
  const allKeys = () => (keys ? keys.value : [])
  const allSelected = computed(() => allKeys().length > 0 && allKeys().every((k) => selected.value.includes(k)))
  function toggle(k) {
    selected.value = selected.value.includes(k) ? selected.value.filter((x) => x !== k) : [...selected.value, k]
  }
  function toggleAll() {
    selected.value = allSelected.value
      ? selected.value.filter((k) => !allKeys().includes(k))
      : [...new Set([...selected.value, ...allKeys()])]
  }
  return { selected, allSelected, toggle, toggleAll }
}

// Pagination over a reactive source list. resetDeps snap back to page 1 (filter,
// query, sort changes); the clamp keeps `page` in range as the list shrinks.
export function usePagination(source, resetDeps = []) {
  const page = ref(1)
  const perPage = ref(50)
  const pageCount = computed(() => Math.max(1, Math.ceil(source.value.length / perPage.value)))
  const paged = computed(() => source.value.slice((page.value - 1) * perPage.value, page.value * perPage.value))
  if (resetDeps.length) watch(resetDeps, () => { page.value = 1 })
  watch(pageCount, (count) => { if (page.value > count) page.value = count })
  return { page, perPage, pageCount, paged }
}

// A watchable YouTube link for any row shape (workspace item or library entry).
export function ytUrl(row) {
  const id = row?.youtube_id || row?.yt_id
  return row?.youtube_url || (id ? `https://www.youtube.com/watch?v=${id}` : null)
}

// Swap the basename (either separator) in a relative path — for in-place rename patches.
export function withNewBase(path, name) { return String(path || '').replace(/[^\\/]+$/, name) }

// Context-menu item definitions, shared so Workspace and Library stay identical.
// Each tab renders these through ActionMenu and handles @select with its own
// dispatcher. FILE_MENU_ITEMS is a function because the delete row is optional
// (Library manages deletion; Workspace does not) and its label depends on source.
export const YT_MENU_ITEMS = [
  { action: 'open', icon: 'mdi-open-in-new', title: 'Open in new tab' },
  { action: 'copy', icon: 'mdi-content-copy', title: 'Copy link' },
  { action: 'embed', icon: 'mdi-youtube', title: 'Play embed' },
  { action: 'ytaudio', icon: 'mdi-music', title: 'Play audio' },
]

// YouTube label menu with the id spelled out in a Copy-ID row (dynamic per row).
// `canFindLocal` adds the interactive "Find local file" picker (no/stale local file).
export function ytMenuItems(ytId, { canFindLocal = false, canSetLocal = false } = {}) {
  const items = [...YT_MENU_ITEMS]
  if (ytId) items.push({ action: 'copyid', icon: 'mdi-identifier', title: `Copy ID · ${ytId}` })
  if (ytId) items.push({ action: 'download', icon: 'mdi-download', title: 'Download audio…' })
  if (ytId) items.push({ action: 'verify-link', icon: 'mdi-link-variant', title: 'Verify link' })
  if (canFindLocal) items.push({ action: 'find-local', icon: 'mdi-folder-search-outline', title: 'Find local file' })
  if (canSetLocal) items.push({ action: 'pick-local', icon: 'mdi-file-music-outline', title: 'Pick local file…' })
  return items
}

// Membership label menus (the row action hub — these replaced the 3-dots menu).
// Context hides self-referential rows: no "Show in Library" on the Library screen,
// no "Send to workspace" when already staged there, etc. (see the design note in tabs).
export function libraryLabelMenu({ trackId, onScreen, inWorkspace } = {}) {
  const items = [{ action: 'info', icon: 'mdi-information-outline', title: 'Info' }]
  if (!inWorkspace) items.push({ action: 'send-workspace', icon: 'mdi-send', title: 'Send to workspace' })
  items.push({ action: 'review', icon: 'mdi-eye-check-outline', title: 'Review' })
  if (onScreen !== 'library') items.push({ action: 'show-library', icon: 'mdi-bookshelf', title: `Show in Library · #${trackId}` })
  items.push({ action: 'remove-library', icon: 'mdi-delete-outline', color: 'error', title: 'Remove from library' })
  return items
}

// `staged` = the entity is actually a Workspace item. Review shows this menu for the
// track under review even when it isn't staged (staged:false) — the lyrics/metadata
// actions still work (on the track), and the membership ops become "Send to workspace".
export function workspaceLabelMenu({ onScreen, inLibrary, staged = true } = {}) {
  const items = [{ action: 'info', icon: 'mdi-information-outline', title: 'Info' }]
  if (staged && !inLibrary) items.push({ action: 'save-library', icon: 'mdi-bookmark-plus-outline', title: 'Save to library' })
  if (!staged) items.push({ action: 'send-workspace', icon: 'mdi-send', title: 'Send to workspace' })
  // Lyrics + metadata finding live here (the row's "workspace" hub); bulk versions are
  // toolbar buttons. find-* fetch by (artist,title) exactly like Find on YouTube.
  items.push({ action: 'find-lyrics', icon: 'mdi-text-search', title: 'Find lyrics' })
  items.push({ action: 'view-lyrics', icon: 'mdi-script-text-outline', title: 'View lyrics' })
  items.push({ action: 'find-metadata', icon: 'mdi-tag-search-outline', title: 'Find metadata' })
  if (staged && onScreen !== 'workspace') items.push({ action: 'show-workspace', icon: 'mdi-briefcase-variant-outline', title: 'Show in Workspace' })
  if (staged) items.push({ action: 'remove-workspace', icon: 'mdi-delete-outline', color: 'error', title: 'Remove from workspace' })
  return items
}

// One dispatcher for the membership-label actions, shared by all screens. The two
// destructive removes are injected because each screen owns its own confirm flow;
// the rest (nav, review, send/save) are identical everywhere so they live here once.
export function useMembershipActions({ onError = () => {}, onNotice = () => {}, reload = async () => {}, removeLibrary = () => {}, removeWorkspace = () => {} } = {}) {
  async function run(action, label) {
    try {
      if (action === 'review') reviewTrack(await api.track(label.trackId))
      else if (action === 'show-library') showInLibrary([label.trackId])
      else if (action === 'show-workspace') showInWorkspace([label.workspaceItemId])
      else if (action === 'send-workspace') { await api.workspaceLibrary(label.trackId); invalidateData(); await reload() }
      else if (action === 'save-library') { await api.workspaceSaveToLibrary([label.workspaceItemId]); invalidateData(); await reload() }
      else if (action === 'find-lyrics') { const r = await api.entityFindLyrics('workspace', label.workspaceItemId); invalidateData(); await reload(); onNotice(r.found ? 'Found lyrics.' : 'No lyrics found.') }
      else if (action === 'find-metadata') { await api.entityFindMetadata('workspace', label.workspaceItemId); invalidateData(); await reload(); onNotice('Applied MusicBrainz metadata.') }
      else if (action === 'remove-library') removeLibrary(label.trackId)
      else if (action === 'remove-workspace') removeWorkspace(label.workspaceItemId)
      // 'view-lyrics' is handled per screen (needs a dialog) — see each tab's onMemberMenu.
    } catch (e) { onError(e) }
  }
  return { run }
}

// Interactive search-picker state + apply, shared by "Find on YouTube" / "Find local
// file". Each screen opens it with a target {kind:'track'|'workspace', id}; on pick it
// applies the choice to that entity, then reloads. The dialog itself is SearchPickerDialog.
export function useSearchPicker({ onError = () => {}, reload = async () => {} } = {}) {
  const picker = ref({ open: false, mode: 'youtube', title: '', query: '', artist: '', songTitle: '', target: null })
  function openYoutube({ title, artist = '', songTitle = '', target }) {
    picker.value = { open: true, mode: 'youtube', title, query: [artist, songTitle].filter(Boolean).join(' '), artist, songTitle, target }
  }
  function openLocal({ title, query = '', target }) {
    picker.value = { open: true, mode: 'local', title, query, artist: '', songTitle: '', target }
  }
  async function onPick(result) {
    const { mode, target } = picker.value
    try {
      if (mode === 'youtube') {
        const body = { youtube_id: result.id, title: result.title, channel: result.channel, view_count: result.view_count }
        await (target.kind === 'track' ? api.trackSetYoutube(target.id, body) : api.workspaceSetYoutube(target.id, body))
      } else {
        const body = { folder_identity: result.folder_identity, relative_path: result.relative_path }
        await (target.kind === 'track' ? api.trackSetLocal(target.id, body) : api.workspaceSetLocal(target.id, body))
      }
      picker.value.open = false
      invalidateData(); await reload()
    } catch (e) { onError(e) }
  }
  return { picker, openYoutube, openLocal, onPick }
}

// Force-set with verification + confirmation: "Set YouTube link…" (paste id/URL, verify
// alive) and "Pick local file…" (native picker, allows files outside configured folders).
// Both show the match score before the user confirms (ForceSetDialog).
export function useForceSet({ onError = () => {}, reload = async () => {} } = {}) {
  const fset = ref({ open: false, mode: 'youtube', entity: null, value: '', resolving: false, resolved: null, error: '', basename: '', score: null, path: '' })
  function openSetYoutube(entity) {
    fset.value = { open: true, mode: 'youtube', entity, value: '', resolving: false, resolved: null, error: '', basename: '', score: null, path: '' }
  }
  async function pickLocalFile(entity) {
    try {
      const { paths } = await api.pickFiles()
      if (!paths?.length) return
      const s = await api.scoreLocal({ path: paths[0], artist: entity.artist, title: entity.title })
      fset.value = { open: true, mode: 'local', entity, value: '', resolving: false, resolved: s, error: '', basename: s.basename, score: s.score, path: s.path }
    } catch (e) { onError(e) }
  }
  // Editing the pasted value invalidates a prior verification, forcing a re-check.
  function setValue(v) { fset.value.value = v; fset.value.resolved = null; fset.value.error = '' }

  // Save auto-verifies then asks for confirmation. Phase 1 (no live result yet): resolve
  // the id/URL — a dead/private link fails inline, an alive one shows its match score and
  // waits. Phase 2 (already verified alive): apply. Local mode applies straight away.
  async function apply() {
    const { mode, entity, path } = fset.value
    fset.value.error = ''
    try {
      if (mode === 'local') {
        await (entity.kind === 'track' ? api.trackSetLocal(entity.id, { path }) : api.workspaceSetLocal(entity.id, { path }))
      } else if (!fset.value.resolved?.alive) {
        fset.value.resolving = true
        try { fset.value.resolved = await api.resolveYoutube({ value: fset.value.value, artist: entity.artist, title: entity.title }) }
        finally { fset.value.resolving = false }
        if (!fset.value.resolved.alive) fset.value.error = `Link is ${fset.value.resolved.health || 'unreachable'} — not set.`
        return                                   // show score / error, wait for confirm
      } else {
        const r = fset.value.resolved
        const body = { youtube_id: r.id, title: r.title, channel: r.channel, view_count: r.view_count }
        await (entity.kind === 'track' ? api.trackSetYoutube(entity.id, body) : api.workspaceSetYoutube(entity.id, body))
      }
      fset.value.open = false; invalidateData(); await reload()
    } catch (e) { fset.value.error = String(e) }
  }
  return { fset, openSetYoutube, pickLocalFile, apply, setValue }
}

// Shared audio-download flow for every download button (bulk + YouTube-label single).
// Opens the format modal, starts a download run (yt-ids, replace-on-success), polls it to
// completion, exposes a dismissable run for DownloadRunAlert, and reloads on finish so the
// Downloaded label appears. Used by Library/Review/Workspace (yt-label). Workspace's *bulk*
// download keeps its own richer run alert but reuses FormatDialog.
export function useAudioDownload({ onError = () => {}, onNotice = () => {}, reload = async () => {} } = {}) {
  const fmtDialog = ref({ open: false, ytIds: [], replace: true, busy: false })
  const dlRun = ref(null)
  let poll = null
  const stopPoll = () => { if (poll) { clearInterval(poll); poll = null } }
  onScopeDispose(stopPoll)

  function askDownload(ytIds, { replace = true } = {}) {
    const ids = [...new Set((ytIds || []).filter(Boolean))]
    if (!ids.length) { onError('No YouTube id to download.'); return }
    fmtDialog.value = { open: true, ytIds: ids, replace, busy: false }
  }
  async function chooseFormat(format) {
    const { ytIds, replace } = fmtDialog.value
    fmtDialog.value.busy = true
    try {
      dlRun.value = await api.downloadRun(ytIds, format, replace)
      fmtDialog.value.open = false
      onNotice(`Downloading ${ytIds.length} item(s) as ${format}…`)
      startPoll()
    } catch (e) { onError(String(e).includes('409') ? 'A download is already running.' : e) }
    finally { fmtDialog.value.busy = false }
  }
  function startPoll() {
    stopPoll()
    poll = setInterval(async () => {
      if (!dlRun.value?.id) return stopPoll()
      try {
        const was = ACTIVE_RUN_STATUSES.includes(dlRun.value.status)
        dlRun.value = await api.workspaceRun(dlRun.value.id)
        if (was && !ACTIVE_RUN_STATUSES.includes(dlRun.value.status)) {
          stopPoll(); onNotice(`Download ${dlRun.value.status}.`); await reload()
        }
      } catch (e) { stopPoll(); onError(e) }
    }, 1500)
  }
  const dismissRun = () => { stopPoll(); dlRun.value = null }
  return { fmtDialog, dlRun, askDownload, chooseFormat, dismissRun }
}

// Shared file-deletion flows, so Library / Workspace / Review can't drift. Two kinds:
//   • local mp3-folder file  → the safety-gated approved-delete flow (preview → token →
//     typed DELETE → deleteTracks + audit). Operates on track ids; the backend rejects a
//     non-approved / non-unique / unsafe target with 409.
//   • downloaded file        → the app's own output, a simple confirm (downloadDelete).
// Screens bind the returned refs to TypedConfirmDialog (local) + ConfirmDialog (download).
export function useLocalDelete({ onError = () => {}, onNotice = () => {}, reload = async () => {} } = {}) {
  const deletePreview = ref(null)     // { token, targets } for the typed-confirm modal
  const deleteBusy = ref(false)
  const deleteOutcome = ref('')
  const audit = ref([])
  const downloadConfirm = ref(null)   // { ytIds } pending simple confirm

  // Library flow: approved tracks by id. Backend gates on check==1/unique/contained.
  async function previewLocal(trackIds) {
    const ids = [...new Set(trackIds.filter(Boolean))]
    if (!ids.length) return
    try {
      const r = await api.deletePreview(ids)
      deletePreview.value = { ...r, _kind: 'library' }
    } catch (e) { onError(e) }
  }
  // Workspace flow: by item id, resolves each item's mp3-folder file server-side (no approval
  // gate; download/out-of-folder items are skipped). Only opens the confirm if something is
  // actually deletable, so an all-skipped selection just notifies instead of a "Delete 0" modal.
  async function previewWorkspaceLocal(itemIds) {
    const ids = [...new Set(itemIds.filter(Boolean))]
    if (!ids.length) return
    try {
      const r = await api.workspaceLocalDeletePreview(ids)
      if (!r.targets.length) { onNotice('No deletable local files in the selection (download or out-of-folder files are skipped).'); return }
      deletePreview.value = { ...r, _kind: 'workspace', _ids: ids }
    } catch (e) { onError(e) }
  }
  async function confirmLocal() {
    if (!deletePreview.value) return
    const p = deletePreview.value
    deleteBusy.value = true
    try {
      const r = p._kind === 'workspace'
        ? await api.workspaceLocalDelete({ ids: p._ids, token: p.token, confirm: 'DELETE' })
        : await api.deleteTracks({ track_ids: p.targets.map((t) => t.track_id), token: p.token, confirm: 'DELETE' })
      deleteOutcome.value = deletionMessage(r)
      deletePreview.value = null
      audit.value = (await api.deleteAudit()).audit
      invalidateData(); await reload()
    } catch (e) {
      deleteOutcome.value = 'Deletion did not complete. Targets reloaded; review audit before retrying.'
      onError(e); deletePreview.value = null
      try { audit.value = (await api.deleteAudit()).audit; await reload() } catch (e2) { onError(e2) }
    } finally { deleteBusy.value = false }
  }
  function askDownloadDelete(ytIds) {
    const ids = [...new Set((ytIds || []).filter(Boolean))]
    if (ids.length) downloadConfirm.value = { ytIds: ids }
  }
  async function confirmDownloadDelete() {
    const ids = downloadConfirm.value?.ytIds || []
    downloadConfirm.value = null
    try { await api.downloadDelete(ids); onNotice('Downloaded file(s) deleted.'); invalidateData(); await reload() }
    catch (e) { onError(e) }
  }
  return { deletePreview, deleteBusy, deleteOutcome, audit, downloadConfirm,
           previewLocal, previewWorkspaceLocal, confirmLocal, askDownloadDelete, confirmDownloadDelete }
}

export const STATUS_MENU_ITEMS = [
  { action: 'info', icon: 'mdi-information-outline', title: 'Decision info' },
  { action: 'unreview', icon: 'mdi-restore', title: 'Set unreviewed' },
  { action: 'rereview', icon: 'mdi-eye-refresh-outline', title: 'Re-review' },
]

// Read-only decision info (approve/reject + the reviewer's verified-parts checklist).
const CHECKLIST_LABELS = { youtube: 'YouTube', local: 'Local file', lyrics: 'Lyrics', metadata: 'Metadata' }
export function decisionInfo(d) {
  if (!d || d.decision == null) return { title: 'Decision', lines: [['Status', 'no decision recorded']] }
  const checked = d.checklist || []
  const when = d.ts ? new Date(d.ts).toLocaleString() : '—'   // decision ts is local ISO
  const lines = [['Decision', d.decision === 1 ? 'Approved' : 'Rejected'], ['When', when]]
  for (const [key, label] of Object.entries(CHECKLIST_LABELS)) lines.push([label, checked.includes(key) ? '✓ verified' : '—'])
  return { title: 'Decision checklist', lines }
}

// Untracked-file label menu — identical on every screen (Import, Library, Workspace):
// exactly Add to Library + Send to Workspace. `canSend` hides Send in the Workspace view
// (the file is already there). Kept minimal on purpose; play/reveal live on other labels.
export function untrackedMenuItems({ canSend = true } = {}) {
  const items = [{ action: 'add', icon: 'mdi-plus-box', title: 'Add to Library' }]
  if (canSend) items.push({ action: 'send', icon: 'mdi-send', title: 'Send to Workspace' })
  return items
}

// `canFindYoutube` adds the interactive "Find on YouTube" picker (local file, no link).
// `canEmbed` adds "Embed metadata" (write our artist/title into the file's tags).
export function fileMenuItems({ deletable = false, source = 'local', canFindYoutube = false, canSetYoutube = false, canEmbed = false } = {}) {
  const items = [
    { action: 'play', icon: 'mdi-play', title: 'Play audio' },
    { action: 'info', icon: 'mdi-information-outline', title: 'File info' },
    { action: 'reveal', icon: 'mdi-folder-open-outline', title: 'Show in folder' },
    { action: 'verify-local', icon: 'mdi-file-check-outline', title: 'Verify file' },
  ]
  if (canFindYoutube) items.push({ action: 'find-youtube', icon: 'mdi-youtube', title: 'Find on YouTube' })
  if (canSetYoutube) items.push({ action: 'set-youtube', icon: 'mdi-link-plus', title: 'Set YouTube link…' })
  if (canEmbed) items.push({ action: 'embed-metadata', icon: 'mdi-tag-text-outline', title: 'Embed metadata' })
  items.push({ action: 'romanize-filename', icon: 'mdi-syllabary-hiragana', title: 'Romanize filename' })
  if (deletable) items.push({
    action: 'delete', icon: 'mdi-delete-outline', color: 'error',
    title: source === 'download' ? 'Delete downloaded file' : 'Delete local file',
  })
  return items
}

// Downloaded-file label menu (the download folder, distinct from an mp3-folder local
// file). `deletable` adds delete (Library manages download-file deletion; Workspace not).
export function downloadMenuItems({ deletable = false } = {}) {
  const items = [
    { action: 'play', icon: 'mdi-play', title: 'Play audio' },
    { action: 'reveal', icon: 'mdi-folder-open-outline', title: 'Show in folder' },
    { action: 'verify-download', icon: 'mdi-file-check-outline', title: 'Verify download' },
    { action: 'embed-metadata', icon: 'mdi-tag-text-outline', title: 'Embed metadata' },
    { action: 'romanize-filename', icon: 'mdi-syllabary-hiragana', title: 'Romanize filename' },
  ]
  if (deletable) items.push({ action: 'delete', icon: 'mdi-delete-outline', color: 'error', title: 'Delete downloaded file' })
  return items
}

// Inline media-preview state shared by every list screen. Rows expose a stable
// `key`; the preview key is `${row.key}|${mode}`. Split on the LAST '|' so keys
// that themselves contain '|' (Import's folder|path fkey) still parse. Media
// sources are read off the row so previewFor never needs per-screen branches:
//   ytId -> embed / yt-audio,  fileSrc -> local audio,  downloadSrc -> download.
export function usePreview() {
  const previewKey = ref(null)
  function toggle(row, mode) {
    const k = `${row.key}|${mode}`
    previewKey.value = previewKey.value === k ? null : k
  }
  function previewFor(row) {
    if (!previewKey.value) return null
    const cut = previewKey.value.lastIndexOf('|')
    // Coerce: toggle() builds the key via a template literal (stringifies), but
    // row.key may be a number (Workspace uses item.id) — compare like-for-like.
    if (previewKey.value.slice(0, cut) !== String(row.key)) return null
    const mode = previewKey.value.slice(cut + 1)
    if (mode === 'embed') return { mode: 'embed', id: row.ytId }
    if (mode === 'ytaudio') return { mode: 'audio', src: row.ytId ? api.ytAudioUrl(row.ytId) : null }
    if (mode === 'local') return { mode: 'audio', src: row.fileSrc }
    if (mode === 'download') return { mode: 'audio', src: row.downloadSrc }
    return null
  }
  return { previewKey, toggle, previewFor }
}

// The one place each list action's LOGIC lives, so a fix lands everywhere at
// once. The three dispatchers switch on the menu action and read everything
// entity-specific off the row (ytUrl, trackId, setCheck, revealArg, infoFor,
// media srcs) — so Workspace / Library / Import differ only in how their
// row-normalizer fills those, never in what an action does. `deleteFile` is
// injected because delete is genuinely per-screen (Library's approved/download
// flows; Workspace/Import have none -> default no-op).
export function useRowActions({ onError = () => {}, onNotice = () => {}, openReview = () => {}, deleteFile = () => {}, reload = () => {} } = {}) {
  const preview = usePreview()
  const fileInfo = ref(null)   // bind to <InfoDialog v-model>

  function ytAction(mode, row) {
    if (!row) return
    if (mode === 'open') { if (row.ytUrl) window.open(row.ytUrl, '_blank', 'noopener') }
    else if (mode === 'copy') { if (row.ytUrl) navigator.clipboard?.writeText(row.ytUrl) }
    else if (mode === 'copyid') { if (row.ytId) navigator.clipboard?.writeText(row.ytId) }
    else if (mode === 'embed') preview.toggle(row, 'embed')
    else if (mode === 'ytaudio') preview.toggle(row, 'ytaudio')
    else if (mode === 'verify-link') verify(row, 'link')
  }
  // Per-row label verification. Reads row.verifyKind/verifyId (each screen's normalizer sets
  // 'track'|'workspace' + id); no-ops on rows with no verifiable entity (e.g. untracked files).
  const VERIFY = {
    link: [api.entityVerifyLink, (r) => `Link is ${r.health}.`],
    local: [api.entityVerifyLocal, (r) => r.present ? 'Local file present.' : 'Local file missing — label cleared.'],
    download: [api.entityVerifyDownload, (r) => r.present ? 'Downloaded file present.' : 'Downloaded file missing.'],
  }
  async function verify(row, aspect) {
    if (!row?.verifyKind || !row?.verifyId) return
    const [call, message] = VERIFY[aspect]
    try { const r = await call(row.verifyKind, row.verifyId); onNotice(message(r)); await reload() }
    catch (e) { onError(e) }
  }
  // Write our metadata into the row's local/downloaded file. `row.embed(source)` picks the
  // entity's endpoint (track vs workspace item), so this stays one code path per screen.
  async function embed(row, source) {
    if (!row?.embed) return
    try { const r = await row.embed(source); onNotice(`Embedded metadata into ${(r.path || '').split(/[\\/]/).pop()}.`) }
    catch (e) { onError(e) }
  }
  // Rename the row's file so its name is romanized (CJK -> Latin), in place. Screens that
  // supply row.onRenamed patch just that row (no list refetch); others fall back to reload.
  async function romanizeFilename(row, source) {
    if (!row?.revealArg) return
    try {
      const r = await api.romanizeFilename(row.revealArg(source))
      onNotice(r.renamed ? `Renamed to ${r.name}.` : 'Filename already romanized.')
      if (r.renamed) { if (row.onRenamed) row.onRenamed(r.name, source); else reload() }
    } catch (e) { onError(e) }
  }
  function fileAction(mode, row, source = 'local') {
    if (!row) return
    if (mode === 'play') preview.toggle(row, source === 'download' ? 'download' : 'local')
    else if (mode === 'info') fileInfo.value = row.infoFor(source)
    else if (mode === 'reveal') api.reveal(row.revealArg(source)).catch(onError)
    else if (mode === 'verify-local') verify(row, 'local')
    else if (mode === 'verify-download') verify(row, 'download')
    else if (mode === 'embed-metadata') embed(row, source)
    else if (mode === 'romanize-filename') romanizeFilename(row, source)
    else if (mode === 'delete') deleteFile(row, source)
  }
  async function statusAction(mode, row) {
    if (!row?.trackId) return
    if (mode === 'info') { try { fileInfo.value = decisionInfo(await api.trackDecision(row.trackId)) } catch (e) { onError(e) } }
    else if (mode === 'unreview') { try { await api.unreviewTrack(row.trackId); row.setCheck(null) } catch (e) { onError(e) } }
    else if (mode === 'rereview') openReview(row)
  }
  return { preview, fileInfo, ytAction, fileAction, statusAction }
}
