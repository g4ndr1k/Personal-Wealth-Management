<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useFinanceStore } from '../stores/finance.js'
import { api } from '../api/client.js'
import { formatIDR } from '../utils/currency.js'
import AuditCompleteness from './AuditCompleteness.vue'

const store = useFinanceStore()

// ── Tab state ──────────────────────────────────────────────────────────────
const activeTab = ref('call-over')

// ── Ignored Transactions state ─────────────────────────────────────────────
const ignoredLoading = ref(false)
const ignoredError = ref('')
const ignoredTxns = ref([])
const ignoredTotal = ref(0)
const ignoredOffset = ref(0)
const IGNORED_PAGE_SIZE = 100

async function loadIgnored(reset = true) {
  if (reset) ignoredOffset.value = 0
  ignoredLoading.value = true
  ignoredError.value = ''
  try {
    const res = await api.transactions({
      category: 'Ignored',
      limit: IGNORED_PAGE_SIZE,
      offset: ignoredOffset.value,
    })
    ignoredTotal.value = res.total || 0
    if (reset) {
      ignoredTxns.value = res.transactions || []
    } else {
      ignoredTxns.value = [...ignoredTxns.value, ...(res.transactions || [])]
    }
  } catch (e) {
    ignoredError.value = e.message || 'Failed to load ignored transactions'
  } finally {
    ignoredLoading.value = false
  }
}

function loadMoreIgnored() {
  ignoredOffset.value += IGNORED_PAGE_SIZE
  loadIgnored(false)
}

// ── Call Over state ────────────────────────────────────────────────────────
const loading = ref(false)
const error = ref('')
const monthA = ref('')   // earlier month (YYYY-MM-DD)
const monthB = ref('')   // later month (YYYY-MM-DD)
const monthALabel = ref('')
const monthBLabel = ref('')
const balancesA = ref([])
const balancesB = ref([])
const holdingsA = ref([])
const holdingsB = ref([])

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt(n) { return formatIDR(n ?? 0) }

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function fmtMonth(d) {
  if (!d) return ''
  const [y, m] = d.split('-')
  return `${MONTHS[parseInt(m, 10) - 1]} ${y}`
}

function monthKey(d) { return (d || '').slice(0, 7) }

function formatAccountType(t) {
  return { savings: 'Savings', checking: 'Checking', money_market: 'Money Market', physical_cash: 'Physical Cash' }[t] || t
}

function formatAssetClass(t) {
  return {
    bond: 'Bond', stock: 'Stock', mutual_fund: 'Mutual Fund',
    retirement: 'Retirement', crypto: 'Crypto',
    real_estate: 'Real Estate', vehicle: 'Vehicle', gold: 'Gold', other: 'Other',
  }[t] || t
}

// ── Resolve two latest months from dashboard range ─────────────────────────
async function resolveMonths() {
  try {
    const dates = await api.wealthSnapshotDates()
    if (!dates || !dates.length) return false

    const start = store.dashboardStartMonth
    const end = store.dashboardEndMonth
    // Deduplicate to one date per month, keeping the latest date per month
    const byMonth = new Map()
    for (const d of dates) {
      const mk = monthKey(d)
      if (mk >= start && mk <= end) {
        if (!byMonth.has(mk) || d > byMonth.get(mk)) {
          byMonth.set(mk, d)
        }
      }
    }
    const months = [...byMonth.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, d]) => d)

    if (months.length < 2) return false

    monthA.value = months[months.length - 2]
    monthB.value = months[months.length - 1]
    monthALabel.value = fmtMonth(monthA.value)
    monthBLabel.value = fmtMonth(monthB.value)
    return true
  } catch {
    return false
  }
}

// ── Load data for both months ──────────────────────────────────────────────
async function loadCallOver() {
  loading.value = true
  error.value = ''
  try {
    const ok = await resolveMonths()
    if (!ok) {
      error.value = 'Need at least 2 months of snapshot data in the selected range.'
      return
    }
    const [bA, bB, hA, hB] = await Promise.all([
      api.getBalances({ snapshot_date: monthA.value }),
      api.getBalances({ snapshot_date: monthB.value }),
      api.getHoldings({ snapshot_date: monthA.value }),
      api.getHoldings({ snapshot_date: monthB.value }),
    ])
    balancesA.value = bA || []
    balancesB.value = bB || []
    holdingsA.value = hA || []
    holdingsB.value = hB || []
  } catch (e) {
    error.value = e.message || 'Failed to load comparison data'
  } finally {
    loading.value = false
  }
}

// ── Build unified comparison rows ──────────────────────────────────────────

// Asset groups and their ordering
const GROUP_ORDER = ['Cash & Liquid', 'Investments', 'Real Estate', 'Physical Assets']
const GROUP_ICONS = {
  'Cash & Liquid': '🏦',
  'Investments': '📈',
  'Real Estate': '🏠',
  'Physical Assets': '🟡',
}

function normalizeBalances(list) {
  const map = {}
  for (const b of list) {
    const key = `${b.institution}::${b.account}::${b.owner || ''}`
    map[key] = {
      label: `${b.institution} — ${b.account}`,
      sub: [formatAccountType(b.account_type), b.owner].filter(Boolean).join(' · '),
      valueA: 0,
      valueB: 0,
      group: 'Cash & Liquid',
    }
  }
  return map
}

function normalizeHoldings(list) {
  const map = {}
  for (const h of list) {
    const key = `${h.asset_class}::${h.asset_name}::${h.institution || ''}::${h.owner || ''}`
    map[key] = {
      label: h.asset_name,
      sub: [formatAssetClass(h.asset_class), h.institution, h.owner].filter(Boolean).join(' · '),
      valueA: 0,
      valueB: 0,
      group: h.asset_group || 'Investments',
    }
  }
  return map
}

const comparisonRows = computed(() => {
  // Build maps from both months
  const allKeys = new Map()

  // Balances A
  const balMapA = normalizeBalances(balancesA.value)
  for (const [k, v] of Object.entries(balMapA)) {
    if (!allKeys.has(k)) allKeys.set(k, { ...v })
    allKeys.get(k).valueA = balancesA.value.find(b =>
      `${b.institution}::${b.account}::${b.owner || ''}` === k
    )?.balance_idr || 0
  }
  // Balances B
  for (const b of balancesB.value) {
    const k = `${b.institution}::${b.account}::${b.owner || ''}`
    if (!allKeys.has(k)) {
      allKeys.set(k, {
        label: `${b.institution} — ${b.account}`,
        sub: [formatAccountType(b.account_type), b.owner].filter(Boolean).join(' · '),
        valueA: 0,
        valueB: 0,
        group: 'Cash & Liquid',
      })
    }
    allKeys.get(k).valueB = b.balance_idr || 0
  }

  // Holdings A
  for (const h of holdingsA.value) {
    const k = `${h.asset_class}::${h.asset_name}::${h.institution || ''}::${h.owner || ''}`
    if (!allKeys.has(k)) {
      allKeys.set(k, {
        label: h.asset_name,
        sub: [formatAssetClass(h.asset_class), h.institution, h.owner].filter(Boolean).join(' · '),
        valueA: 0,
        valueB: 0,
        group: h.asset_group || 'Investments',
      })
    }
    allKeys.get(k).valueA = h.market_value_idr || 0
  }
  // Holdings B
  for (const h of holdingsB.value) {
    const k = `${h.asset_class}::${h.asset_name}::${h.institution || ''}::${h.owner || ''}`
    if (!allKeys.has(k)) {
      allKeys.set(k, {
        label: h.asset_name,
        sub: [formatAssetClass(h.asset_class), h.institution, h.owner].filter(Boolean).join(' · '),
        valueA: 0,
        valueB: 0,
        group: h.asset_group || 'Investments',
      })
    }
    allKeys.get(k).valueB = h.market_value_idr || 0
  }

  // Add delta to each row
  const rows = [...allKeys.values()].map(r => ({
    ...r,
    delta: r.valueB - r.valueA,
  }))

  // Group by asset group
  const grouped = {}
  for (const g of GROUP_ORDER) grouped[g] = []
  for (const r of rows) {
    if (!grouped[r.group]) grouped[r.group] = []
    grouped[r.group].push(r)
  }

  // Sort within groups: by absolute delta descending (biggest movers first)
  for (const g of Object.keys(grouped)) {
    grouped[g].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
  }

  return grouped
})

const totalA = computed(() => {
  return balancesA.value.reduce((s, b) => s + (b.balance_idr || 0), 0)
    + holdingsA.value.reduce((s, h) => s + (h.market_value_idr || 0), 0)
})
const totalB = computed(() => {
  return balancesB.value.reduce((s, b) => s + (b.balance_idr || 0), 0)
    + holdingsB.value.reduce((s, h) => s + (h.market_value_idr || 0), 0)
})
const totalDelta = computed(() => totalB.value - totalA.value)

// ── Lifecycle ──────────────────────────────────────────────────────────────
onMounted(() => { loadCallOver() })

watch([() => store.dashboardStartMonth, () => store.dashboardEndMonth], () => {
  loadCallOver()
})

watch(activeTab, (tab) => {
  if (tab === 'ignored' && !ignoredTxns.value.length && !ignoredLoading.value) {
    loadIgnored()
  }
})
</script>

<template>
  <div class="audit-root">
    <!-- Tab bar -->
    <div class="audit-tabs">
      <button
        :class="['audit-tab', activeTab === 'call-over' && 'active']"
        @click="activeTab = 'call-over'"
      >📊 Call Over</button>
      <button
        :class="['audit-tab', activeTab === 'pdf' && 'active']"
        @click="activeTab = 'pdf'"
      >📋 PDF</button>
      <button
        :class="['audit-tab', activeTab === 'ignored' && 'active']"
        @click="activeTab = 'ignored'"
      >🚫 Ignored</button>
    </div>

    <!-- PDF Completeness tab -->
    <AuditCompleteness v-if="activeTab === 'pdf'" />

    <!-- Ignored Transactions tab -->
    <template v-if="activeTab === 'ignored'">
      <div class="ign-header">
        <div class="ign-header-row">
          <h1 class="co-title">🚫 Ignored Transactions</h1>
          <button class="btn btn-ghost btn-sm" :disabled="ignoredLoading" @click="loadIgnored()">
            {{ ignoredLoading ? 'Loading…' : 'Refresh' }}
          </button>
        </div>
        <p class="co-subtitle">
          Transactions excluded from all income/expense/category calculations (e.g. Permata RDN custodian account movements).
        </p>
      </div>

      <div v-if="ignoredLoading && !ignoredTxns.length" class="loading">
        <div class="spinner"></div> Loading ignored transactions…
      </div>

      <div v-else-if="ignoredError" class="alert alert-error" style="margin:0 16px">
        ⚠️ {{ ignoredError }}
        <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="loadIgnored()">Retry</button>
      </div>

      <template v-else>
        <div class="ign-count">
          {{ ignoredTotal.toLocaleString() }} ignored transaction{{ ignoredTotal !== 1 ? 's' : '' }}
        </div>

        <!-- Table header -->
        <div class="ign-table-header">
          <div class="ign-col-date">Date</div>
          <div class="ign-col-inst">Institution</div>
          <div class="ign-col-desc">Description</div>
          <div class="ign-col-owner">Owner</div>
          <div class="ign-col-amt">Amount (IDR)</div>
        </div>

        <div
          v-for="tx in ignoredTxns"
          :key="tx.id || tx.hash"
          class="ign-row"
        >
          <div class="ign-col-date">{{ tx.date }}</div>
          <div class="ign-col-inst">{{ tx.institution }}</div>
          <div class="ign-col-desc">
            <span class="ign-desc-text">{{ tx.raw_description }}</span>
            <span v-if="tx.merchant" class="ign-merchant">{{ tx.merchant }}</span>
          </div>
          <div class="ign-col-owner">{{ tx.owner }}</div>
          <div class="ign-col-amt" :class="tx.amount >= 0 ? 'text-income' : 'text-expense'">
            {{ fmt(tx.amount) }}
          </div>
        </div>

        <div v-if="ignoredTxns.length < ignoredTotal" class="ign-load-more">
          <button class="btn btn-ghost btn-sm" :disabled="ignoredLoading" @click="loadMoreIgnored">
            {{ ignoredLoading ? 'Loading…' : `Load more (${ignoredTxns.length} / ${ignoredTotal})` }}
          </button>
        </div>

        <div v-if="!ignoredTxns.length" class="ign-empty">
          No ignored transactions found.
        </div>
      </template>
    </template>

    <!-- Call Over tab -->
    <template v-if="activeTab === 'call-over'">
      <!-- Header -->
      <div class="co-header">
        <div class="co-header-row">
          <h1 class="co-title">📊 Call Over</h1>
          <button class="btn btn-ghost btn-sm" :disabled="loading" @click="loadCallOver">
            {{ loading ? 'Loading…' : 'Refresh' }}
          </button>
        </div>
        <p class="co-subtitle">
          Side-by-side asset comparison to identify balance changes between months.
        </p>
      </div>

      <!-- Loading -->
      <div v-if="loading && !balancesA.length && !holdingsA.length" class="loading">
        <div class="spinner"></div> Loading comparison…
      </div>

      <!-- Error -->
      <div v-else-if="error" class="alert alert-error" style="margin:0 16px">
        ⚠️ {{ error }}
        <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="loadCallOver">Retry</button>
      </div>

      <!-- Comparison -->
      <template v-else>
        <!-- Summary bar -->
        <div class="co-summary">
          <div class="co-summary-card">
            <div class="co-summary-label">{{ monthALabel }}</div>
            <div class="co-summary-value">{{ fmt(totalA) }}</div>
          </div>
          <div class="co-summary-arrow">
            <span :class="totalDelta >= 0 ? 'text-income' : 'text-expense'">
              {{ totalDelta >= 0 ? '▲' : '▼' }} {{ fmt(Math.abs(totalDelta)) }}
            </span>
          </div>
          <div class="co-summary-card">
            <div class="co-summary-label">{{ monthBLabel }}</div>
            <div class="co-summary-value">{{ fmt(totalB) }}</div>
          </div>
        </div>

        <!-- Asset groups -->
        <div v-for="group in GROUP_ORDER" :key="group">
          <template v-if="comparisonRows[group]?.length">
            <div class="co-group-header">
              <span>{{ GROUP_ICONS[group] }} {{ group }}</span>
            </div>

            <!-- Table header -->
            <div class="co-table-header">
              <div class="co-col-asset">Asset</div>
              <div class="co-col-val">{{ monthALabel }}</div>
              <div class="co-col-val">{{ monthBLabel }}</div>
              <div class="co-col-val">Variance</div>
            </div>

            <!-- Rows -->
            <div v-for="(row, idx) in comparisonRows[group]" :key="idx" class="co-row">
              <div class="co-col-asset">
                <span class="co-asset-name">{{ row.label }}</span>
                <span v-if="row.sub" class="co-asset-sub">{{ row.sub }}</span>
              </div>
              <div class="co-col-val">
                <span :class="row.valueA === 0 && row.valueB !== 0 ? 'co-muted' : ''">
                  {{ row.valueA === 0 && row.valueB !== 0 ? '—' : fmt(row.valueA) }}
                </span>
              </div>
              <div class="co-col-val">
                <span :class="row.valueB === 0 && row.valueA !== 0 ? 'co-muted' : ''">
                  {{ row.valueB === 0 && row.valueA !== 0 ? '—' : fmt(row.valueB) }}
                </span>
              </div>
              <div class="co-col-val">
                <span v-if="row.delta !== 0" :class="row.delta > 0 ? 'co-pos' : 'co-neg'">
                  {{ row.delta > 0 ? '▲' : '▼' }} {{ fmt(Math.abs(row.delta)) }}
                </span>
                <span v-else class="co-flat">—</span>
              </div>
            </div>

            <!-- Group subtotal -->
            <div class="co-subtotal">
              <span class="co-col-asset">Subtotal</span>
              <span class="co-col-val co-subtotal-val">
                {{ fmt(comparisonRows[group].reduce((s, r) => s + r.valueA, 0)) }}
              </span>
              <span class="co-col-val co-subtotal-val">
                {{ fmt(comparisonRows[group].reduce((s, r) => s + r.valueB, 0)) }}
              </span>
              <span class="co-col-val co-subtotal-val" :class="
                comparisonRows[group].reduce((s, r) => s + r.delta, 0) > 0 ? 'co-pos'
                : comparisonRows[group].reduce((s, r) => s + r.delta, 0) < 0 ? 'co-neg' : ''
              ">
                <template v-if="comparisonRows[group].reduce((s, r) => s + r.delta, 0) !== 0">
                  {{ comparisonRows[group].reduce((s, r) => s + r.delta, 0) > 0 ? '▲' : '▼' }}
                  {{ fmt(Math.abs(comparisonRows[group].reduce((s, r) => s + r.delta, 0))) }}
                </template>
                <template v-else>—</template>
              </span>
            </div>
          </template>
        </div>

        <!-- Grand total -->
        <div class="co-grand-total">
          <span class="co-col-asset">Total Assets</span>
          <span class="co-col-val co-grand-val">{{ fmt(totalA) }}</span>
          <span class="co-col-val co-grand-val">{{ fmt(totalB) }}</span>
          <span class="co-col-val co-grand-val" :class="totalDelta > 0 ? 'co-pos' : totalDelta < 0 ? 'co-neg' : ''">
            {{ totalDelta > 0 ? '▲' : totalDelta < 0 ? '▼' : '' }} {{ fmt(Math.abs(totalDelta)) }}
          </span>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* ── Tab bar ─────────────────────────────────────────────────────────────── */
.audit-root {
  max-width: 100%;
}
.audit-tabs {
  display: flex;
  gap: 0;
  padding: 0 16px 8px;
}
.audit-tab {
  flex: 1;
  padding: 10px 8px;
  border: 1.5px solid var(--border);
  background: var(--card);
  color: var(--neutral);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
  white-space: nowrap;
}
.audit-tab:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
.audit-tab:last-child  { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.audit-tab + .audit-tab { border-left: none; }
.audit-tab.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

/* ── Call Over header ────────────────────────────────────────────────────── */
.co-header {
  padding: 8px 16px 12px;
}
.co-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.co-title {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.03em;
  margin: 0;
}
.co-subtitle {
  font-size: 12px;
  color: var(--text-muted);
  margin: 4px 0 0;
}

/* ── Summary bar ─────────────────────────────────────────────────────────── */
.co-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 8px;
}
.co-summary-card {
  flex: 1;
  padding: 10px 14px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
}
.co-summary-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.co-summary-value {
  font-size: 16px;
  font-weight: 800;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
.co-summary-arrow {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  text-align: center;
  line-height: 1.3;
}

/* ── Group header ────────────────────────────────────────────────────────── */
.co-group-header {
  display: flex;
  align-items: center;
  padding: 10px 16px 6px;
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
}

/* ── Table ───────────────────────────────────────────────────────────────── */
.co-table-header {
  display: flex;
  align-items: center;
  padding: 6px 16px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 2px solid var(--border);
}
.co-row {
  display: flex;
  align-items: center;
  padding: 9px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--card);
  transition: background 0.1s;
}
.co-row:active { background: var(--primary-dim); }

.co-col-asset {
  flex: 1.4;
  min-width: 0;
}
.co-col-val {
  flex: 1;
  text-align: right;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text);
  white-space: nowrap;
  padding-left: 8px;
}
.co-asset-name {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.co-asset-sub {
  display: block;
  font-size: 11px;
  color: var(--neutral);
  margin-top: 1px;
}

/* ── Variance colors ─────────────────────────────────────────────────────── */
.co-pos { color: #16a34a; }
.co-neg { color: #dc2626; }
.co-flat { color: var(--text-muted); }
.co-muted { color: var(--text-muted); opacity: 0.5; }

/* ── Subtotals ───────────────────────────────────────────────────────────── */
.co-subtotal {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 2px solid var(--border);
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
  background: var(--card);
}
.co-subtotal-val {
  font-size: 12px !important;
  color: var(--text-muted);
}

/* ── Grand total ─────────────────────────────────────────────────────────── */
.co-grand-total {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  margin-bottom: 24px;
  font-size: 14px;
  font-weight: 800;
  color: var(--text);
  background: var(--card);
  border-top: 2.5px solid var(--border);
  border-bottom: 2.5px solid var(--border);
}
.co-grand-val {
  font-size: 14px !important;
  font-weight: 800 !important;
  color: var(--text) !important;
}

/* ── Ignored Transactions tab ───────────────────────────────────────────── */
.ign-header {
  padding: 8px 16px 12px;
}
.ign-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ign-count {
  padding: 6px 16px 8px;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 600;
}
.ign-table-header {
  display: flex;
  align-items: center;
  padding: 6px 16px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 2px solid var(--border);
}
.ign-row {
  display: flex;
  align-items: flex-start;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--card);
  font-size: 12px;
}
.ign-row:active { background: var(--primary-dim); }
.ign-col-date  { flex: 0 0 88px; color: var(--text-muted); padding-top: 1px; }
.ign-col-inst  { flex: 0 0 80px; font-weight: 600; color: var(--text); padding-top: 1px; }
.ign-col-desc  { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.ign-col-owner { flex: 0 0 64px; color: var(--neutral); padding-top: 1px; }
.ign-col-amt   {
  flex: 0 0 100px;
  text-align: right;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  padding-top: 1px;
}
.ign-desc-text {
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ign-merchant {
  font-size: 11px;
  color: var(--text-muted);
  font-style: italic;
}
.ign-load-more {
  padding: 12px 16px;
  text-align: center;
}
.ign-empty {
  padding: 32px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

/* ── Desktop dark overrides ──────────────────────────────────────────────── */
:deep(.desktop-shell) .co-title {
  color: var(--text);
}
:deep(.desktop-shell) .co-subtitle {
  color: var(--text-muted);
}
:deep(.desktop-shell) .co-pos {
  color: #4ade80;
}
:deep(.desktop-shell) .co-neg {
  color: #f87171;
}
</style>
