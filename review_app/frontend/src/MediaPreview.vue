<script setup>
// Inline preview shared by Workspace and Library rows. preview is the object
// returned by each screen's previewFor(): { mode:'embed', id } or { mode:'audio', src }.
import { youtubeEmbed } from './review'
defineProps({ preview: { type: Object, default: null } })
</script>

<template>
  <div v-if="preview" class="px-4 pb-2">
    <iframe v-if="preview.mode === 'embed' && preview.id" :src="youtubeEmbed(preview.id)" width="100%" height="220" style="border:0;border-radius:8px" allow="encrypted-media" allowfullscreen />
    <audio v-else-if="preview.src" :src="preview.src" controls preload="none" style="width:100%" />
  </div>
</template>
