<script setup>
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()
</script>

<template>
  <nav class="bottom-nav">
    <RouterLink to="/" class="nav-item">
      <span class="nav-icon">📊</span>
      <span class="nav-label">Dashboard</span>
    </RouterLink>
    <RouterLink to="/flows" class="nav-item">
      <span class="nav-icon">📈</span>
      <span class="nav-label">Flows</span>
    </RouterLink>
    <RouterLink to="/wealth" class="nav-item">
      <span class="nav-icon">💰</span>
      <span class="nav-label">Wealth</span>
    </RouterLink>
    <RouterLink to="/holdings" class="nav-item">
      <span class="nav-icon">🗂️</span>
      <span class="nav-label">Assets</span>
    </RouterLink>
    <RouterLink to="/transactions" class="nav-item">
      <span class="nav-icon">🧾</span>
      <span class="nav-label">Txns</span>
    </RouterLink>
    <RouterLink to="/review" class="nav-item">
      <span class="nav-icon">🔎</span>
      <span class="nav-label">Review</span>
      <span v-if="store.reviewCount > 0" class="nav-badge">
        {{ store.reviewCount > 99 ? '99+' : store.reviewCount }}
      </span>
    </RouterLink>
    <RouterLink to="/settings" class="nav-item">
      <span class="nav-icon">⚙︎</span>
      <span class="nav-label">More</span>
    </RouterLink>
  </nav>
</template>
