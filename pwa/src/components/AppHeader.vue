<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useFinanceStore } from '../stores/finance.js'
import { useOfflineSync } from '../composables/useOfflineSync.js'

const store = useFinanceStore()
const route = useRoute()
const { isOnline } = useOfflineSync(() => store.loadHealth({ forceFresh: true }))
const pageTitle = computed(() => route.meta?.title || 'Personal Finance')
</script>

<template>
  <header class="top-bar">
    <div class="title-block">
      <span class="title-eyebrow">Personal Finance</span>
      <span class="title">{{ pageTitle }}</span>
    </div>
    <div class="sync-info">
      <button
        class="hide-toggle"
        :title="store.hideNumbers ? 'Show amounts' : 'Hide amounts'"
        @click="store.setHideNumbers(!store.hideNumbers)"
      >{{ store.hideNumbers ? '🙈' : '👁' }}</button>
      <span class="status-dot" :class="{ ok: store.health?.status === 'ok' && isOnline }"></span>
      <span v-if="store.health">
        {{ store.health.transaction_count }} txn
        <span v-if="store.isReadOnly" class="ro-indicator" title="Read-only · NAS replica">👁</span>
        <template v-if="store.reviewCount > 0"> · {{ store.reviewCount }} pending</template>
      </span>
      <span v-else>connecting…</span>
    </div>
  </header>
</template>

<style scoped>
.ro-indicator {
  font-size: 13px;
  margin-left: 2px;
  opacity: 0.7;
  vertical-align: 1px;
}
.hide-toggle {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
  opacity: 0.8;
  line-height: 1;
}
.hide-toggle:hover {
  opacity: 1;
}
</style>
