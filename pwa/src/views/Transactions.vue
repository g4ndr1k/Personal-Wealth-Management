<template>
  <div>
    <div class="section-hd">📋 Transactions</div>

    <!-- Filters -->
    <div class="filter-bar">
      <select v-model="filters.year" @change="onFilterChange">
        <option value="">All Years</option>
        <option v-for="y in store.years" :key="y" :value="y">{{ y }}</option>
      </select>
      <select v-model="filters.month" @change="onFilterChange" :disabled="!filters.year">
        <option value="">All Months</option>
        <option v-for="m in 12" :key="m" :value="m">{{ monthName(m) }}</option>
      </select>
    </div>
    <div class="filter-bar">
      <select v-model="filters.owner" @change="onFilterChange">
        <option value="">All Owners</option>
        <option v-for="o in store.owners" :key="o" :value="o">{{ o }}</option>
      </select>
      <select v-model="filters.category" @change="onFilterChange">
        <option value="">All Categories</option>
        <option v-for="c in store.categoryNames" :key="c" :value="c">{{ c }}</option>
      </select>
    </div>
    <div class="filter-bar">
      <input
        v-model="filters.q"
        placeholder="🔍 Search description or merchant…"
        @input="debouncedSearch"
      />
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading"><div class="spinner"></div> Loading…</div>

    <!-- Error -->
    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <template v-else>
      <!-- Count + totals bar -->
      <div v-if="totalCount > 0" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:12px;color:var(--text-muted)">
        <span>{{ totalCount.toLocaleString() }} transaction{{ totalCount !== 1 ? 's' : '' }}</span>
        <span>
          <span class="text-income">+{{ fmt(totals.income) }}</span>
          &nbsp;
          <span class="text-expense">-{{ fmt(Math.abs(totals.expense)) }}</span>
        </span>
      </div>

      <!-- Transaction list -->
      <div v-if="!transactions.length" class="empty-state">
        <div class="e-icon">📭</div>
        <div class="e-msg">No transactions found</div>
        <div class="e-sub">Try adjusting your filters</div>
      </div>
      <div v-else class="tx-list">
        <template v-for="tx in transactions" :key="tx.hash">
          <!-- Row -->
          <div
            class="tx-row"
            :class="{ expanded: expandedHash === tx.hash }"
            @click="toggle(tx)"
          >
            <div class="tx-main">
              <div class="tx-merchant">{{ tx.merchant || tx.raw_description }}</div>
              <div class="tx-cat">
                <span v-if="tx.category">{{ catIcon(tx.category) }} {{ tx.category }}</span>
                <span v-else style="color:var(--warning)">⚠ Uncategorised</span>
                · {{ tx.owner }}
              </div>
            </div>
            <div class="tx-right">
              <div class="tx-amount" :class="tx.amount >= 0 ? 'text-income' : 'text-expense'">
                {{ fmt(tx.amount) }}
              </div>
              <div class="tx-date">{{ tx.date }}</div>
            </div>
          </div>
          <!-- Expanded detail panel -->
          <div v-if="expandedHash === tx.hash" class="tx-detail-panel">
            <div class="detail-grid">
              <div class="detail-item">
                <div class="dk">Raw description</div>
                <div class="dv">{{ tx.raw_description }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Institution</div>
                <div class="dv">{{ tx.institution || '—' }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Account</div>
                <div class="dv">{{ tx.account || '—' }}</div>
              </div>
              <div v-if="tx.original_currency" class="detail-item">
                <div class="dk">Foreign amount</div>
                <div class="dv">{{ tx.original_amount }} {{ tx.original_currency }} @ {{ tx.exchange_rate }}</div>
              </div>
              <div v-if="tx.notes" class="detail-item" style="grid-column:1/-1">
                <div class="dk">Notes</div>
                <div class="dv">{{ tx.notes }}</div>
              </div>
              <div class="detail-item" style="grid-column:1/-1">
                <div class="dk">Hash</div>
                <div class="dv" style="font-family:monospace;font-size:10px">{{ tx.hash }}</div>
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- Pagination -->
      <div v-if="totalCount > pageSize" class="pagination">
        <button class="btn btn-ghost btn-sm" :disabled="page === 0" @click="goPage(page - 1)">‹ Prev</button>
        <span>{{ page + 1 }} / {{ totalPages }}</span>
        <button class="btn btn-ghost btn-sm" :disabled="page >= totalPages - 1" @click="goPage(page + 1)">Next ›</button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()

const transactions = ref([])
const totalCount   = ref(0)
const loading      = ref(false)
const error        = ref(null)
const expandedHash = ref(null)
const page         = ref(0)
const pageSize     = 50

const totals = ref({ income: 0, expense: 0 })

const filters = ref({
  year:     '',
  month:    '',
  owner:    '',
  category: '',
  q:        '',
})

const totalPages = computed(() => Math.max(1, Math.ceil(totalCount.value / pageSize)))

const MONTHS_LONG = ['January','February','March','April','May','June','July','August','September','October','November','December']
function monthName(m) { return MONTHS_LONG[m - 1] }
function catIcon(name) { return store.categoryMap[name]?.icon || '📁' }

function fmt(n) {
  if (n === null || n === undefined) return 'Rp 0'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000_000) return `${sign}Rp ${(abs / 1_000_000_000).toFixed(1)} M`
  if (abs >= 1_000_000)     return `${sign}Rp ${(abs / 1_000_000).toFixed(1)} jt`
  return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(n)
}

let searchTimer = null
function debouncedSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { page.value = 0; load() }, 350)
}

function onFilterChange() {
  page.value = 0
  load()
}

function goPage(p) {
  page.value = p
  load()
}

function toggle(tx) {
  expandedHash.value = expandedHash.value === tx.hash ? null : tx.hash
}

async function load() {
  loading.value = true
  error.value   = null
  expandedHash.value = null
  try {
    const params = {
      limit:  pageSize,
      offset: page.value * pageSize,
    }
    if (filters.value.year)     params.year     = filters.value.year
    if (filters.value.month)    params.month    = filters.value.month
    if (filters.value.owner)    params.owner    = filters.value.owner
    if (filters.value.category) params.category = filters.value.category
    if (filters.value.q)        params.q        = filters.value.q

    const res = await api.transactions(params)
    transactions.value = res.transactions || []
    totalCount.value   = res.total_count  || 0

    // Compute income/expense totals from this page's data
    let inc = 0, exp = 0
    for (const tx of transactions.value) {
      if (tx.amount >= 0) inc += tx.amount
      else                exp += tx.amount
    }
    totals.value = { income: inc, expense: exp }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
