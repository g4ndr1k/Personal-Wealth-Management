<script setup>
import { computed, onMounted } from 'vue'
import { useFinanceStore } from './stores/finance.js'
import { useLayout } from './composables/useLayout.js'
import MobileShell from './layouts/MobileShell.vue'
import DesktopShell from './layouts/DesktopShell.vue'

const store = useFinanceStore()
const { isDesktop } = useLayout()

function shouldForceFreshBootstrap() {
  try {
    const mode = window.localStorage.getItem('pwa_layout_mode')
    if (mode === 'desktop') return true
    if (mode === 'mobile') return false
  } catch {}
  return window.matchMedia('(min-width: 1024px)').matches
}

onMounted(async () => {
  await store.bootstrap(shouldForceFreshBootstrap() ? { forceFresh: true } : {})
  await store.loadHealth({ forceFresh: true })
})

const shell = computed(() => (isDesktop.value ? DesktopShell : MobileShell))
</script>

<template>
  <component :is="shell" />
</template>
