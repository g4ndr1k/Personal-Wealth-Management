<script setup>
import { useFmt } from '../composables/useFmt.js'

defineProps({
  items: { type: Array, default: () => [] },
  selectedHash: { type: String, default: '' },
})

defineEmits(['select'])

const { fmt } = useFmt()
</script>

<template>
  <div class="rw-workspace">
    <div class="rw-list">
      <div class="rw-list-hd">
        {{ items.length }} transaction{{ items.length !== 1 ? 's' : '' }} pending
      </div>
      <button
        v-for="item in items"
        :key="item.hash"
        type="button"
        class="rw-item"
        :class="{ selected: selectedHash === item.hash }"
        @click="$emit('select', item)"
      >
        <div class="rw-item-desc">{{ item.raw_description }}</div>
        <div class="rw-item-meta">{{ item.date }} · {{ item.owner }} · {{ item.institution }}</div>
        <div class="rw-item-amount" :class="item.amount >= 0 ? 'text-income' : 'text-expense'">
          {{ fmt(item.amount) }}
        </div>
      </button>
    </div>

    <div class="rw-detail">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.rw-workspace {
  display: grid;
  grid-template-columns: 380px minmax(0, 1fr);
  gap: 16px;
  min-height: 400px;
}

.rw-list {
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow-y: auto;
  max-height: calc(100vh - 120px);
}

.rw-list-hd {
  position: sticky;
  top: 0;
  background: var(--bg);
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  border-bottom: 2px solid var(--border);
}

.rw-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 12px 14px;
  border: none;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  transition: background 0.1s;
}

.rw-item:hover { background: var(--bg); }

.rw-item.selected {
  background: var(--primary-dim);
  border-left: 3px solid var(--primary);
}

.rw-item:last-child { border-bottom: none; }

.rw-item-desc {
  font-weight: 700;
  font-size: 13px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rw-item-meta {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.rw-item-amount {
  font-weight: 800;
  font-size: 13px;
  margin-top: 4px;
}

.rw-detail {
  background: var(--card);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
  min-height: 300px;
  color: var(--text);
}
</style>
