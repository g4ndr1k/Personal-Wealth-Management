<script setup>
import { ref, computed } from 'vue'
import { useFmt } from '../composables/useFmt.js'

const props = defineProps({
  transactions: { type: Array, required: true },
  selectedHash: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['select'])

const sortKey = ref('date')
const sortAsc = ref(false)

const columns = [
  { key: 'date', label: 'Date', width: '95px', sortable: true },
  { key: 'raw_description', label: 'Description', width: 'auto', sortable: true },
  { key: 'merchant', label: 'Merchant', width: '150px', sortable: true },
  { key: 'category', label: 'Category', width: '130px', sortable: true },
  { key: 'amount', label: 'Amount (IDR)', width: '130px', sortable: true, align: 'right' },
  { key: 'owner', label: 'Owner', width: '85px', sortable: true },
  { key: 'institution', label: 'Bank', width: '100px', sortable: true },
  { key: 'original_currency', label: 'FX', width: '55px', sortable: false },
]

const sorted = computed(() => {
  if (!sortKey.value) return props.transactions
  const k = sortKey.value
  const dir = sortAsc.value ? 1 : -1
  return [...props.transactions].sort((a, b) => {
    const va = a[k] ?? ''
    const vb = b[k] ?? ''
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
    return String(va).localeCompare(String(vb)) * dir
  })
})

function toggleSort(col) {
  if (!col.sortable) return
  if (sortKey.value === col.key) sortAsc.value = !sortAsc.value
  else {
    sortKey.value = col.key
    sortAsc.value = true
  }
}

const { fmt } = useFmt()
</script>

<template>
  <div class="dt-table-wrap">
    <table class="dt-table">
      <thead>
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            :style="{ width: col.width, textAlign: col.align || 'left' }"
            :class="{ sortable: col.sortable, active: sortKey === col.key }"
            @click="toggleSort(col)"
          >
            {{ col.label }}
            <span v-if="col.sortable && sortKey === col.key" class="sort-arrow">
              {{ sortAsc ? '▲' : '▼' }}
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="loading">
          <td :colspan="columns.length" class="dt-empty">
            <div class="spinner" style="display:inline-block;width:16px;height:16px;vertical-align:middle;margin-right:6px"></div>
            Loading…
          </td>
        </tr>
        <tr v-else-if="!sorted.length">
          <td :colspan="columns.length" class="dt-empty">
            <div style="font-size:28px;margin-bottom:8px">📭</div>
            No transactions found
          </td>
        </tr>
        <template v-else>
          <tr
            v-for="tx in sorted"
            :key="tx.hash"
            :class="{
              selected: selectedHash === tx.hash,
              'row-income': tx.amount >= 0,
              'row-uncat': !tx.category,
            }"
            @click="emit('select', tx)"
          >
            <td class="dt-date">{{ tx.date }}</td>
            <td class="dt-desc" :title="tx.raw_description">{{ tx.raw_description }}</td>
            <td>{{ tx.merchant || '—' }}</td>
            <td>
              <span v-if="tx.category" class="dt-cat-chip">{{ tx.category }}</span>
              <span v-else class="dt-uncat-chip">Uncategorised</span>
            </td>
            <td style="text-align:right" :class="tx.amount >= 0 ? 'text-income' : 'text-expense'">
              {{ fmt(tx.amount) }}
            </td>
            <td>{{ tx.owner }}</td>
            <td>{{ tx.institution }}</td>
            <td class="dt-fx">
              <template v-if="tx.original_currency">{{ tx.original_currency }}</template>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.dt-table-wrap {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow-x: auto;
  background: var(--card);
  box-shadow: var(--shadow);
}

.dt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.dt-table th {
  position: sticky;
  top: 0;
  background: var(--bg);
  border-bottom: 2px solid var(--border);
  padding: 10px 12px;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  white-space: nowrap;
  user-select: none;
}

.dt-table th.sortable { cursor: pointer; }
.dt-table th.sortable:hover { color: var(--primary); }
.dt-table th.active { color: var(--primary); }
.sort-arrow { font-size: 9px; margin-left: 3px; }

.dt-table td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  vertical-align: middle;
}

.dt-table tbody tr { cursor: pointer; transition: background 0.1s; }
.dt-table tbody tr:hover { background: var(--bg); }
.dt-table tbody tr.selected { background: var(--primary-dim); }
.dt-table tbody tr:last-child td { border-bottom: none; }

.dt-date {
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
}

.dt-desc {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.dt-fx {
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 600;
}

.row-income td:first-child { box-shadow: inset 3px 0 0 var(--income); }
.row-uncat td:first-child { box-shadow: inset 3px 0 0 var(--warning); }

.dt-cat-chip,
.dt-uncat-chip {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.dt-cat-chip {
  background: #e0f2fe;
  color: #0369a1;
}

.dt-uncat-chip {
  background: #fef3c7;
  color: #92400e;
}

.dt-empty {
  text-align: center;
  padding: 40px 20px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
