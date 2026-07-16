// Tiny shared app-nav store so the Library list can hand a track to the Review
// view and switch tabs, without prop-drilling through App.
import { ref } from 'vue'

export const activeTab = ref('review')
export const focusTrack = ref(null)   // a full track row to review right now, or null

export function reviewTrack(track) {
  focusTrack.value = track
  activeTab.value = 'review'
}
