<template>
  <div>
    <div class="section-hd">💱 Foreign Spend</div>

    <!-- Filters -->
    <div class="filter-bar">
      <select v-model="filters.year" @change="load">
        <option value="">All Years</option>
        <option v-for="y in store.years" :key="y" :value="y">{{ y }}</option>
      </select>
      <select v-model="filters.month" @change="load" :disabled="!filters.year">
        <option value="">All Months</option>
        <option v-for="m in 12" :key="m" :value="m">{{ monthName(m) }}</option>
      </select>
      <select v-model="filters.owner" @change="load">
        <option value="">All Owners</option>
        <option v-for="o in store.owners" :key="o" :value="o">{{ o }}</option>
      </select>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading"><div class="spinner"></div> Loading…</div>

    <!-- Error -->
    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <!-- Empty -->
    <div v-else-if="!rows.length" class="empty-state">
      <div class="e-icon">🌐</div>
      <div class="e-msg">No foreign transactions</div>
      <div class="e-sub">No foreign-currency spending found for the selected period.</div>
    </div>

    <template v-else>
      <!-- Summary cards -->
      <div class="summary-grid" style="margin-bottom:12px">
        <div class="summary-card">
          <div class="s-label">Currencies</div>
          <div class="s-value text-neutral">{{ currencies.length }}</div>
        </div>
        <div class="summary-card">
          <div class="s-label">Total (IDR)</div>
          <div class="s-value text-expense">{{ fmt(Math.abs(totalIDR)) }}</div>
        </div>
      </div>

      <!-- Per-currency groups -->
      <div v-for="ccy in currencies" :key="ccy" class="fx-group">
        <div class="fx-group-hd">
          <span>{{ ccyFlag(ccy) }} {{ ccy }}</span>
          <span>{{ rows.filter(r => r.original_currency === ccy).length }} transactions
            · {{ fmt(Math.abs(ccyTotalIDR(ccy))) }} IDR equiv.</span>
        </div>
        <table class="fx-table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Merchant</th>
              <th>Category</th>
              <th class="num">{{ ccy }}</th>
              <th class="num">IDR</th>
              <th class="num">Rate</th>
              <th>Owner</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows.filter(rx => rx.original_currency === ccy)" :key="r.hash ?? r.date + r.amount">
              <td>{{ r.date }}</td>
              <td>{{ r.merchant || r.raw_description }}</td>
              <td>{{ r.category ? (catIcon(r.category) + ' ' + r.category) : '—' }}</td>
              <td class="num text-expense">{{ fmtFX(r.original_amount) }}</td>
              <td class="num text-expense">{{ fmt(r.amount) }}</td>
              <td class="num" style="color:var(--text-muted);font-size:11px">{{ fmtRate(r.exchange_rate) }}</td>
              <td>{{ r.owner }}</td>
            </tr>
            <!-- Currency subtotal -->
            <tr style="background:var(--bg);font-weight:700">
              <td colspan="3" style="text-align:right;font-size:11px;color:var(--text-muted)">Subtotal</td>
              <td class="num text-expense">{{ fmtFX(ccyTotalFX(ccy)) }}</td>
              <td class="num text-expense">{{ fmt(ccyTotalIDR(ccy)) }}</td>
              <td colspan="2"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()

const rows    = ref([])
const loading = ref(false)
const error   = ref(null)

const filters = ref({ year: '', month: '', owner: '' })

const MONTHS_LONG = ['January','February','March','April','May','June','July','August','September','October','November','December']
function monthName(m) { return MONTHS_LONG[m - 1] }
function catIcon(name) { return store.categoryMap[name]?.icon || '📁' }

// ── Derived ───────────────────────────────────────────────────────────────────
const currencies = computed(() => {
  const s = new Set(rows.value.map(r => r.original_currency).filter(Boolean))
  return [...s].sort()
})

const totalIDR = computed(() =>
  rows.value.reduce((s, r) => s + (r.amount || 0), 0)
)

function ccyTotalFX(ccy) {
  return rows.value
    .filter(r => r.original_currency === ccy)
    .reduce((s, r) => s + (r.original_amount || 0), 0)
}
function ccyTotalIDR(ccy) {
  return rows.value
    .filter(r => r.original_currency === ccy)
    .reduce((s, r) => s + (r.amount || 0), 0)
}

// Flag emoji lookup (common currencies)
const CCY_FLAGS = {
  USD: '🇺🇸', EUR: '🇪🇺', SGD: '🇸🇬', MYR: '🇲🇾', AUD: '🇦🇺',
  JPY: '🇯🇵', GBP: '🇬🇧', HKD: '🇭🇰', THB: '🇹🇭', KRW: '🇰🇷',
  CNY: '🇨🇳', CHF: '🇨🇭', CAD: '🇨🇦', NZD: '🇳🇿', AED: '🇦🇪',
}
function ccyFlag(ccy) { return CCY_FLAGS[ccy] || '🌐' }

// ── Formatters ────────────────────────────────────────────────────────────────
function fmt(n) {
  if (n === null || n === undefined) return 'Rp 0'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1_000_000_000) return `${sign}Rp ${(abs / 1_000_000_000).toFixed(1)} M`
  if (abs >= 1_000_000)     return `${sign}Rp ${(abs / 1_000_000).toFixed(1)} jt`
  return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(n)
}

function fmtFX(n) {
  if (n === null || n === undefined) return '—'
  return new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(n))
}

function fmtRate(n) {
  if (!n) return '—'
  if (n >= 1000) return new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 }).format(n)
  return n.toFixed(4)
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value   = null
  try {
    const params = {}
    if (filters.value.year)  params.year  = filters.value.year
    if (filters.value.month) params.month = filters.value.month
    if (filters.value.owner) params.owner = filters.value.owner
    rows.value = await api.foreignTransactions(params)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
