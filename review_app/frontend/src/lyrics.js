// LRC parsing + active-line lookup. Pure — no Vue, no DOM — so it can be unit-tested.
// Ported from usb-ldac (web/ui/src/lyrics.js), trimmed to what the player needs.

export function parseLyricLines(text) {
  const value = String(text || '')
  if (!value) return []
  return value.split(/\r?\n/).map((line) => {
    const match = line.match(/^\[(\d+):(\d{1,2}(?:\.\d+)?)\](.*)$/)
    return match
      ? { timed: true, time: Number(match[1]) * 60 + Number(match[2]), text: match[3] }
      : { timed: false, time: null, text: line }
  })
}

export function isSynced(lines) {
  return lines.some((line) => line.timed)
}

// Index of the last timed line whose stamp is <= t (the line currently "playing"),
// or -1 before the first stamp. Stamps are assumed to climb (standard LRC).
export function activeLineIndex(lines, t) {
  let idx = -1
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].timed) continue
    if (lines[i].time <= t) idx = i
    else break
  }
  return idx
}
