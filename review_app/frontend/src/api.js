// Thin fetch wrapper. All URLs relative -> Vite proxies /api to FastAPI.
async function jget(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
async function jsend(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
const jpost = (url, body) => jsend('POST', url, body)

export const api = {
  counts: () => jget('/api/counts'),
  rows: (status = 'unreviewed', limit = 200, offset = 0) =>
    jget(`/api/rows?status=${status}&limit=${limit}&offset=${offset}`),
  library: () => jget('/api/library'),
  libraryVerify: (ids = null, limit = 30) => jpost('/api/library/verify', { ids, limit }),
  libraryRemove: (track_ids) => jpost('/api/library/remove', { track_ids }),
  unreviewTrack: (id) => jpost('/api/library/unreview', { track_ids: [id] }),
  track: (id) => jget(`/api/track/${id}`),
  decide: (track_id, decision, checklist) => jpost('/api/decision', { track_id, decision, checklist }),
  trackDecision: (id) => jget(`/api/track/${id}/decision`),
  export: () => jpost('/api/export'),
  audioUrl: (id) => `/api/audio/${id}`,
  ytAudioUrl: (ytId) => `/api/yt_audio/${ytId}`,

  // settings (.env secrets)
  getSettings: () => jget('/api/settings'),
  saveSettings: (body) => jpost('/api/settings', body),
  pickFolder: () => jpost('/api/pick-folder'),
  pickFiles: () => jpost('/api/pick-files'),
  addFilesByPath: (paths, target) => jpost('/api/files/add', { paths, target }),
  stagedFiles: () => jget('/api/files/staged'),

  // discord harvest
  discordFetch: (body) => jpost('/api/discord/fetch', body),

  // playlist generator (paste URLs -> watch_videos playlist links)
  playlists: (text) => jpost('/api/playlists', { text }),

  // Workspace / local-file operations
  workspace: () => jget('/api/workspace'),
  workspaceEnrich: (ids = null, limit = 40) => jpost('/api/workspace/enrich', { ids, limit }),
  workspaceImport: (text) => jpost('/api/workspace/import', { text }),
  workspaceSelection: (ids) => jpost('/api/workspace/selection', { ids }),
  workspacePlaylists: (ids) => jpost('/api/workspace/playlists', { ids }),
  workspaceLibrary: (track_id) => jpost('/api/workspace/library', { track_id }),
  workspaceRemove: (ids) => jsend('DELETE', '/api/workspace', { ids }),
  // Routes each item server-side — file items become Library tracks, link items become
  // saved links. Same endpoint for per-row and bulk so they can't drift.
  workspaceSaveToLibrary: (ids) => jpost('/api/workspace/save-to-library', { ids }),
  workspaceDownload: async (ids, format) => {
    const r = await fetch(`/api/workspace/download/${format}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids }) })
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
    return { blob: await r.blob(), skippedDuplicates: Number(r.headers.get('X-Workspace-Skipped-Duplicate-Count') || 0) }
  },
  savedLinks: () => jget('/api/saved-links'),
  savedLinkMatch: (body) => jpost('/api/saved-links/match', body),
  localFiles: () => jget('/api/local-files'),
  localFileMatch: (folder_identity, relative_path) => jpost('/api/local-files/match', { folder_identity, relative_path }),
  addFilesToLibrary: (files) => jpost('/api/library/add-files', { files }),
  workspaceAddFiles: (files) => jpost('/api/workspace/add-files', { files }),
  localAudioUrl: (folder_identity, relative_path) => `/api/local-audio?folder_identity=${encodeURIComponent(folder_identity)}&relative_path=${encodeURIComponent(relative_path)}`,
  downloadAudioUrl: (yt_id) => `/api/download-audio?yt_id=${encodeURIComponent(yt_id)}`,
  reveal: (body) => jpost('/api/reveal', body),
  downloadDelete: (yt_ids) => jpost('/api/download/delete', { yt_ids }),
  untracked: () => jget('/api/untracked'),
  deletePreview: (track_ids) => jpost('/api/library/delete/preview', { track_ids }),
  deleteTracks: (body) => jpost('/api/library/delete', body),
  // Workspace bulk delete-local: by item id, resolves each item's mp3-folder file server-side
  // (no approval gate; download/out-of-folder files are skipped). Same token/typed-DELETE flow.
  workspaceLocalDeletePreview: (ids) => jpost('/api/workspace/local-delete/preview', { ids }),
  workspaceLocalDelete: (body) => jpost('/api/workspace/local-delete', body),
  deleteAudit: () => jget('/api/library/delete/audit'),
  cleanupPreview: () => jpost('/api/settings/cleanup-downloads/preview'),
  cleanupDownloads: (body) => jpost('/api/settings/cleanup-downloads', body),
  workspaceDownloadRun: (ids, format = 'opus') => jpost('/api/workspace/runs/download', { ids, format }),
  // Download YouTube ids straight to the download folder (the YouTube-label button); replace-on-success.
  downloadRun: (yt_ids, format = 'opus', replace = true) => jpost('/api/download/run', { yt_ids, format, replace }),
  workspaceRun: (id) => jget(`/api/workspace/runs/${id}`),
  workspaceRuns: () => jget('/api/workspace/runs'),

  // background tasks (verify sweeps) + Activity log
  tasks: () => jget('/api/tasks'),
  taskCancel: (id) => jpost(`/api/tasks/${id}/cancel`),
  verifyLibraryTask: (scope, ids = null) => jpost('/api/tasks/verify/library', { scope, ids }),
  verifyWorkspaceTask: (scope, ids = null) => jpost('/api/tasks/verify/workspace', { scope, ids }),
  findYoutubeWorkspaceTask: (ids = null) => jpost('/api/tasks/find-youtube/workspace', { ids }),
  findLocalWorkspaceTask: (ids = null) => jpost('/api/tasks/find-local/workspace', { ids }),
  findLyricsWorkspaceTask: (ids = null) => jpost('/api/tasks/find-lyrics/workspace', { ids }),
  findMetadataWorkspaceTask: (ids = null) => jpost('/api/tasks/find-metadata/workspace', { ids }),
  reviewFindYoutube: (track_id) => jpost('/api/review/find-youtube', { track_id }),
  // interactive search pickers (ranked candidates, user chooses)
  searchYoutube: (body) => jpost('/api/search/youtube', body),
  searchLocal: (body) => jpost('/api/search/local', body),
  trackSetYoutube: (id, body) => jpost(`/api/track/${id}/youtube`, body),
  workspaceSetYoutube: (id, body) => jpost(`/api/workspace/${id}/youtube`, body),
  trackSetLocal: (id, body) => jpost(`/api/track/${id}/local-file`, body),
  workspaceSetLocal: (id, body) => jpost(`/api/workspace/${id}/local-file`, body),
  // force-set (verify + confirm) + metadata edit
  resolveYoutube: (body) => jpost('/api/resolve/youtube', body),
  scoreLocal: (body) => jpost('/api/score/local', body),
  trackPatch: (id, fields) => jsend('PATCH', `/api/track/${id}`, { fields }),
  workspacePatch: (id, fields) => jsend('PATCH', `/api/workspace/${id}`, { fields }),
  // embed our metadata into the file's tags (source: 'local' | 'download')
  workspaceEmbed: (id, source = 'local') => jpost(`/api/workspace/${id}/embed`, { source }),
  trackEmbed: (id, source = 'local') => jpost(`/api/track/${id}/embed`, { source }),
  // lyrics + metadata finding — generic over kind ('track' | 'workspace'), like /embed.
  // Bulk (workspace) via the *Task methods above.
  entityLyrics: (kind, id, refresh = false) => jget(`/api/${kind}/${id}/lyrics${refresh ? '?refresh=true' : ''}`),
  entityFindLyrics: (kind, id) => jpost(`/api/${kind}/${id}/lyrics`),
  entitySaveLyrics: (kind, id, lyrics) => jpost(`/api/${kind}/${id}/lyrics/save`, { lyrics }),
  entityFindMetadata: (kind, id) => jpost(`/api/${kind}/${id}/find-metadata`),
  entityFileTags: (kind, id) => jget(`/api/${kind}/${id}/file-tags`),
  // per-row label verification (YouTube link health / local-file & downloaded-file existence)
  entityVerifyLink: (kind, id) => jpost(`/api/${kind}/${id}/verify-link`),
  entityVerifyLocal: (kind, id) => jpost(`/api/${kind}/${id}/verify-local`),
  entityVerifyDownload: (kind, id) => jpost(`/api/${kind}/${id}/verify-download`),
  // romanize CJK -> Latin (Hepburn). `texts` in, romanized texts out; filename renames on disk.
  romanize: (texts) => jpost('/api/romanize', { texts }),
  romanizeFilename: (ref) => jpost('/api/romanize/filename', ref),
  history: (limit = 200) => jget(`/api/history?limit=${limit}`),

  // pipeline scripts (background jobs)
  scripts: () => jget('/api/scripts'),
  scriptState: (name, tail) =>
    jget(`/api/scripts/${name}` + (tail ? `?tail=${tail}` : '')),
  scriptRun: (name, args = []) => jpost(`/api/scripts/${name}/run`, { args }),
  scriptStop: (name) => jpost(`/api/scripts/${name}/stop`),
}
