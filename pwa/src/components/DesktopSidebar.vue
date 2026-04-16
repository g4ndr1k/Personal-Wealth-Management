<script setup>
import { useFinanceStore } from '../stores/finance.js'
import { useLayout } from '../composables/useLayout.js'

const store = useFinanceStore()
const { setLayoutMode } = useLayout()
</script>

<template>
  <nav class="desktop-sidebar">
    <div class="desktop-sidebar__brand">
      <div class="desktop-sidebar__title">Personal Finance</div>
      <div class="desktop-sidebar__subtitle">Wealth Dashboard</div>
    </div>

    <div class="desktop-sidebar__nav">
      <RouterLink to="/" class="desktop-sidebar__link">📊 <span>Dashboard</span></RouterLink>
      <RouterLink to="/flows" class="desktop-sidebar__link">📈 <span>Flows</span></RouterLink>
      <RouterLink to="/wealth" class="desktop-sidebar__link">💰 <span>Wealth</span></RouterLink>
      <RouterLink to="/holdings" class="desktop-sidebar__link">🗂️ <span>Assets</span></RouterLink>
      <RouterLink to="/transactions" class="desktop-sidebar__link">🧾 <span>Transactions</span></RouterLink>
      <RouterLink to="/review" class="desktop-sidebar__link">
        🔎 <span>Review</span>
        <span v-if="store.reviewCount > 0" class="desktop-sidebar__badge">
          {{ store.reviewCount > 99 ? '99+' : store.reviewCount }}
        </span>
      </RouterLink>
      <RouterLink to="/foreign" class="desktop-sidebar__link">🌍 <span>Foreign Spend</span></RouterLink>
      <RouterLink to="/adjustment" class="desktop-sidebar__link">🔧 <span>Adjustment</span></RouterLink>
      <RouterLink to="/audit" class="desktop-sidebar__link">📋 <span>Audit</span></RouterLink>
      <RouterLink to="/settings" class="desktop-sidebar__link">⚙️ <span>Settings</span></RouterLink>
    </div>

    <div class="desktop-sidebar__footer">
      <div class="desktop-sidebar__status">
        <span class="status-dot" :class="{ ok: store.health?.status === 'ok' }"></span>
        {{ store.health?.transaction_count ?? '—' }} txn
        <span v-if="store.isReadOnly" class="ro-indicator" title="Read-only · NAS replica">👁</span>
        · Agentic Finance
      </div>
      <button class="desktop-sidebar__mode-btn" @click="setLayoutMode('auto')">
        Auto Layout
      </button>
    </div>
  </nav>
</template>

<style scoped>
.desktop-sidebar__status {
  display: flex;
  align-items: center;
  gap: 7px;
}

.desktop-sidebar__mode-btn {
  margin-top: 10px;
  width: 100%;
  border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.08);
  color: #fff;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}

.desktop-sidebar__mode-btn:hover {
  background: rgba(255,255,255,0.12);
}

.ro-indicator {
  font-size: 12px;
  margin-left: 2px;
  opacity: 0.6;
  vertical-align: 1px;
}
</style>
