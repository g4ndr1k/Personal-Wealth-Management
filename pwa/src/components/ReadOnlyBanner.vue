<script setup>
import { computed } from 'vue'
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()

const lastSync = computed(() => {
  const ts = store.health?.last_sync
  if (!ts) return null
  try {
    const d = new Date(ts)
    const now = new Date()
    const diffMs = now - d
    const diffH = Math.floor(diffMs / 3600000)
    const diffM = Math.floor(diffMs / 60000)
    if (diffM < 2) return 'just now'
    if (diffM < 60) return `${diffM}m ago`
    if (diffH < 24) return `${diffH}h ago`
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  } catch {
    return null
  }
})
</script>

<template>
  <div v-if="store.isReadOnly" class="setting-card ro-card">
    <div class="ro-card__header">
      <span class="ro-card__icon">👁</span>
      <span class="ro-card__label">Read-Only Mode</span>
      <span class="ro-card__badge">NAS Replica</span>
    </div>
    <div class="ro-card__body">
      <div class="ro-card__row">
        <span class="ro-card__key">Connection</span>
        <span class="ro-card__val ro-card__val--live">
          <span class="ro-card__dot"></span> Live
        </span>
      </div>
      <div v-if="lastSync" class="ro-card__row">
        <span class="ro-card__key">Data synced</span>
        <span class="ro-card__val">{{ lastSync }}</span>
      </div>
      <div v-if="store.health?.transaction_count != null" class="ro-card__row">
        <span class="ro-card__key">Transactions</span>
        <span class="ro-card__val">{{ store.health.transaction_count.toLocaleString() }}</span>
      </div>
    </div>
    <div class="ro-card__note">
      This is a read-only copy synced from your Mac. Write actions are disabled.
    </div>
  </div>
</template>

<style scoped>
.ro-card {
  border: 1px solid rgba(37, 99, 235, 0.18);
  background: rgba(37, 99, 235, 0.04);
}

.ro-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.ro-card__icon {
  font-size: 18px;
}

.ro-card__label {
  font-weight: 700;
  font-size: 15px;
  color: var(--text);
}

.ro-card__badge {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
}

.ro-card__body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}

.ro-card__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}

.ro-card__key {
  color: var(--text-muted);
}

.ro-card__val {
  font-weight: 600;
  color: var(--text);
}

.ro-card__val--live {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #16a34a;
}

.ro-card__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #16a34a;
  animation: ro-pulse 2s ease-in-out infinite;
}

@keyframes ro-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.ro-card__note {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.5;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
</style>
