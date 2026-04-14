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
      <span class="status-dot" :class="{ ok: store.health?.status === 'ok' && isOnline }"></span>
      <span v-if="store.health">
        {{ store.health.transaction_count }} txn
        <template v-if="store.reviewCount > 0"> · {{ store.reviewCount }} pending</template>
      </span>
      <span v-else>connecting…</span>
    </div>
  </header>
</template>
