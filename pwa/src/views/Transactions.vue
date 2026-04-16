<template>
  <div>
    <div class="section-hd">📋 Transactions</div>

    <div class="filter-bar" :class="{ 'filters-muted': aiFilters }">
      <select v-model="filters.year" @change="onFilterChange">
        <option value="">All Years</option>
        <option v-for="y in store.years" :key="y" :value="y">{{ y }}</option>
      </select>
      <select v-model="filters.month" @change="onFilterChange" :disabled="!filters.year">
        <option value="">All Months</option>
        <option v-for="m in 12" :key="m" :value="m">{{ monthName(m) }}</option>
      </select>
    </div>
    <div class="filter-bar" :class="{ 'filters-muted': aiFilters }">
      <select v-model="filters.owner" @change="onFilterChange">
        <option value="">All Owners</option>
        <option v-for="o in store.owners" :key="o" :value="o">{{ o }}</option>
      </select>
      <select v-model="filters.categoryGroup" @change="onFilterChange">
        <option value="">All Category Groups</option>
        <option v-for="group in categoryGroupNames" :key="group" :value="group">{{ group }}</option>
      </select>
    </div>
    <div class="filter-bar" :class="{ 'filters-muted': aiFilters }">
      <select v-model="filters.category" @change="onFilterChange">
        <option value="">All Categories</option>
        <option value="__uncategorised__">Uncategorised only</option>
        <option v-for="c in sortedCategoryNames" :key="c" :value="c">{{ c }}</option>
      </select>
    </div>
    <div class="filter-bar" :class="{ 'filters-muted': aiFilters }">
      <input
        v-model="filters.q"
        placeholder="🔍 Search description or merchant…"
        @input="debouncedSearch"
      />
    </div>

    <!-- AI AMA box -->
    <div class="filter-bar ai-ama-bar">
      <div class="ai-ama-wrap" :class="{ loading: aiLoading }">
        <span class="ai-ama-label">✨ AI</span>
        <input
          v-model="aiQuery"
          class="ai-ama-input"
          placeholder="Ask anything: '3 biggest spendings in Jan 2026'…"
          :disabled="aiLoading"
          @keydown.enter.prevent="askAi"
        />
        <span v-if="aiLoading" class="spinner spinner-sm"></span>
      </div>
    </div>

    <!-- Exclude toggle — shown only while AI mode is active -->
    <div v-if="aiFilters" class="filter-bar" style="padding-top:0">
      <label class="ai-exclude-toggle">
        <input type="checkbox" v-model="aiExcludeSystem" @change="load" />
        <span>Exclude transfers &amp; adjustments</span>
      </label>
    </div>

    <!-- AI mode active banner -->
    <div v-if="aiFilters" class="ai-active-banner">
      <span class="ai-active-icon">✨</span>
      <span class="ai-active-query">"{{ aiLabel }}"</span>
      <button class="btn btn-ghost btn-sm ai-clear-btn" @click="clearAi">✕ Clear</button>
    </div>

    <!-- AI error toast -->
    <div v-if="aiError" class="alert alert-error ai-error-toast">{{ aiError }}</div>

    <div v-if="loading" class="loading"><div class="spinner"></div> Loading…</div>

    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <template v-else>
      <div v-if="totalCount > 0" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:12px;color:var(--text-muted)">
        <span>{{ totalCount.toLocaleString() }} transaction{{ totalCount !== 1 ? 's' : '' }}</span>
        <span>
          <span class="text-income">{{ fmt(totals.income) }}</span>
          &nbsp;
          <span class="text-expense">{{ fmt(Math.abs(totals.expense)) }}</span>
        </span>
      </div>

      <div v-if="!transactions.length" class="empty-state">
        <div class="e-icon">📭</div>
        <div class="e-msg">No transactions found</div>
        <div class="e-sub">Try adjusting your filters</div>
      </div>

      <template v-else>
        <TransactionTable
          v-if="isDesktop"
          :transactions="transactions"
          :selected-hash="expandedHash"
          :loading="loading"
          @select="toggle"
        />

        <div v-else class="tx-list">
          <template v-for="tx in transactions" :key="tx.hash">
            <div class="tx-row" :class="{ expanded: expandedHash === tx.hash }" @click="toggle(tx)">
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

              <div class="cat-editor">
                <div class="cat-editor-hd">Change category</div>
                <div class="cat-editor-row">
                  <select v-model="editCategory" class="cat-select" @click.stop>
                    <option value="" disabled>Select category…</option>
                    <option v-for="c in store.categoryNames" :key="c" :value="c">
                      {{ catIcon(c) }} {{ c }}
                    </option>
                  </select>
                  <button
                    class="btn btn-primary btn-sm"
                    :disabled="!editCategory || editCategory === tx.category || saving"
                    @click.stop="saveCategory(tx)"
                  >
                    {{ saving ? 'Saving…' : 'Save' }}
                  </button>
                </div>
                <div v-if="saveError" class="alert alert-error" style="margin-top:6px;padding:6px 8px;font-size:12px">
                  {{ saveError }}
                </div>
                <div v-if="saveSuccess" class="alert alert-success" style="margin-top:6px;padding:6px 8px;font-size:12px">
                  ✓ Category &amp; alias updated {{ typeof saveSuccess === 'string' ? `(${saveSuccess})` : '' }}
                </div>
              </div>
            </div>
          </template>
        </div>

        <div v-if="isDesktop && selectedTx" class="dt-detail-panel">
          <div class="dt-detail-header">
            <span class="dt-detail-title">Transaction Detail</span>
            <button class="btn btn-ghost btn-sm" @click="expandedHash = null">✕ Close</button>
          </div>
          <div class="dt-detail-body">
            <div class="detail-grid">
              <div class="detail-item">
                <div class="dk">Raw description</div>
                <div class="dv">{{ selectedTx.raw_description }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Merchant</div>
                <div class="dv">{{ selectedTx.merchant || '—' }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Institution</div>
                <div class="dv">{{ selectedTx.institution || '—' }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Account</div>
                <div class="dv">{{ selectedTx.account || '—' }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Owner</div>
                <div class="dv">{{ selectedTx.owner }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Date</div>
                <div class="dv">{{ selectedTx.date }}</div>
              </div>
              <div v-if="selectedTx.original_currency" class="detail-item">
                <div class="dk">Foreign amount</div>
                <div class="dv">{{ selectedTx.original_amount }} {{ selectedTx.original_currency }} @ {{ selectedTx.exchange_rate }}</div>
              </div>
              <div v-if="selectedTx.notes" class="detail-item" style="grid-column:1/-1">
                <div class="dk">Notes</div>
                <div class="dv">{{ selectedTx.notes }}</div>
              </div>
              <div class="detail-item" style="grid-column:1/-1">
                <div class="dk">Hash</div>
                <div class="dv" style="font-family:monospace;font-size:10px">{{ selectedTx.hash }}</div>
              </div>
            </div>

            <div class="cat-editor">
              <div class="cat-editor-hd">Change category</div>
              <div class="cat-editor-row">
                <select v-model="editCategory" class="cat-select" @click.stop>
                  <option value="" disabled>Select category…</option>
                  <option v-for="c in store.categoryNames" :key="c" :value="c">
                    {{ catIcon(c) }} {{ c }}
                  </option>
                </select>
                <button
                  class="btn btn-primary btn-sm"
                  :disabled="!editCategory || editCategory === selectedTx.category || saving"
                  @click.stop="saveCategory(selectedTx)"
                >
                  {{ saving ? 'Saving…' : 'Save' }}
                </button>
              </div>
              <div v-if="saveError" class="alert alert-error" style="margin-top:6px;padding:6px 8px;font-size:12px">
                {{ saveError }}
              </div>
              <div v-if="saveSuccess" class="alert alert-success" style="margin-top:6px;padding:6px 8px;font-size:12px">
                ✓ Category &amp; alias updated {{ typeof saveSuccess === 'string' ? `(${saveSuccess})` : '' }}
              </div>
            </div>
          </div>
        </div>
      </template>

      <div v-if="totalCount > pageSize && !aiFilters" class="pagination">
        <button class="btn btn-ghost btn-sm" :disabled="page === 0" @click="goPage(page - 1)">‹ Prev</button>
        <span>{{ page + 1 }} / {{ totalPages }}</span>
        <button class="btn btn-ghost btn-sm" :disabled="page >= totalPages - 1" @click="goPage(page + 1)">Next ›</button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'
import { useLayout } from '../composables/useLayout.js'
import TransactionTable from '../components/TransactionTable.vue'


const store = useFinanceStore()
const { isDesktop } = useLayout()

const transactions = ref([])
const totalCount = ref(0)
const loading = ref(false)
const error = ref(null)
const expandedHash = ref(null)
const page = ref(0)
const pageSize = 50

const totals = ref({ income: 0, expense: 0 })

const filters = ref({
  year: '',
  month: '',
  owner: '',
  categoryGroup: '',
  category: '',
  q: '',
})

const editCategory = ref('')
const saving = ref(false)
const saveError = ref(null)
const saveSuccess = ref(false)

const aiQuery         = ref('')
const aiLoading       = ref(false)
const aiError         = ref(null)
const aiFilters       = ref(null)
const aiLabel         = ref('')
const aiExcludeSystem = ref(true)

const totalPages = computed(() => Math.max(1, Math.ceil(totalCount.value / pageSize)))
const sortedCategoryNames = computed(() => [...store.categoryNames].sort((a, b) => a.localeCompare(b)))
const categoryGroupNames = computed(() => {
  const groups = new Set(
    (store.categories || [])
      .map((category) => category.category_group)
      .filter((group) => !!group)
  )
  return [...groups].sort((a, b) => a.localeCompare(b))
})
const selectedTx = computed(() =>
  expandedHash.value
    ? transactions.value.find(tx => tx.hash === expandedHash.value) || null
    : null
)

const MONTHS_LONG = ['January','February','March','April','May','June','July','August','September','October','November','December']

function monthName(m) { return MONTHS_LONG[m - 1] }
function catIcon(name) { return store.categoryMap[name]?.icon || '📁' }
function fmt(n) { return formatIDR(n) }

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
  if (expandedHash.value === tx.hash) {
    expandedHash.value = null
  } else {
    expandedHash.value = tx.hash
    editCategory.value = tx.category || ''
    saveError.value = null
    saveSuccess.value = false
  }
}

async function saveCategory(tx) {
  saving.value = true
  saveError.value = null
  saveSuccess.value = false
  try {
    const res = await api.patchCategory(tx.hash, { category: editCategory.value, update_alias: true })
    if (res.transaction) {
      tx.category = res.transaction.category
      if (Object.prototype.hasOwnProperty.call(res.transaction, 'notes')) {
        tx.notes = res.transaction.notes
      }
    }
    if (res.also_updated > 0) {
      const raw = tx.raw_description
      for (const t of transactions.value) {
        if (t.hash !== tx.hash && t.raw_description === raw) t.category = editCategory.value
      }
    }
    saveSuccess.value = res.also_updated ? `+ ${res.also_updated} similar` : true
    setTimeout(() => { saveSuccess.value = false }, 3000)
  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

async function load() {
  loading.value = true
  error.value = null
  expandedHash.value = null
  try {
    const params = {}
    const selectedCategory = filters.value.category
    const uncategorisedOnly = selectedCategory === '__uncategorised__'
    if (aiFilters.value) {
      const af = aiFilters.value
      if (af.year)     params.year     = af.year
      if (af.month)    params.month    = af.month
      if (af.owner)    params.owner    = af.owner
      if (af.category) params.category = af.category
      if (af.q)        params.q        = af.q
      params.limit  = 500
      params.offset = 0
    } else {
      params.limit  = pageSize
      params.offset = page.value * pageSize
      if (filters.value.year)     params.year     = filters.value.year
      if (filters.value.month)    params.month    = filters.value.month
      if (filters.value.owner)    params.owner    = filters.value.owner
      if (filters.value.categoryGroup) params.category_group = filters.value.categoryGroup
      if (uncategorisedOnly)      params.uncategorised_only = true
      else if (selectedCategory)  params.category = selectedCategory
      if (filters.value.q)        params.q        = filters.value.q
    }

    const res = await api.transactions(params, { forceFresh: true })
    let txs = res.transactions || []

    if (aiFilters.value) {
      const { sort, limit, income_only, expense_only } = aiFilters.value
      const SYSTEM_CATS = new Set(['Transfer', 'Adjustment', 'Ignored', 'Cash Withdrawal'])
      if (aiExcludeSystem.value) txs = txs.filter(tx => !SYSTEM_CATS.has(tx.category))
      if (income_only) txs = txs.filter(tx => tx.amount >= 0 && !SYSTEM_CATS.has(tx.category))
      if (expense_only) txs = txs.filter(tx => tx.amount < 0 && !SYSTEM_CATS.has(tx.category))
      if (sort === 'amount_asc')  txs.sort((a, b) => a.amount - b.amount)
      if (sort === 'amount_desc') txs.sort((a, b) => b.amount - a.amount)
      if (sort === 'date_asc')    txs.sort((a, b) => a.date.localeCompare(b.date))
      if (sort === 'date_desc')   txs.sort((a, b) => b.date.localeCompare(a.date))
      if (limit > 0) txs = txs.slice(0, limit)
      totalCount.value = txs.length
    } else {
      totalCount.value = res.total ?? res.total_count ?? 0
    }

    transactions.value = txs

    const excludeCats = new Set(['Transfer', 'Adjustment', 'Ignored'])
    let inc = 0
    let exp = 0
    for (const tx of transactions.value) {
      if (excludeCats.has(tx.category)) continue
      if (tx.amount >= 0) inc += tx.amount
      else exp += tx.amount
    }
    totals.value = { income: inc, expense: exp }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function askAi() {
  const query = aiQuery.value.trim()
  if (!query || aiLoading.value) return
  aiLoading.value = true
  aiError.value = null
  try {
    const res = await api.aiQuery(query)
    aiFilters.value = res
    aiLabel.value = query
    page.value = 0
    load()
  } catch (e) {
    console.error('[AI AMA]', e)
    aiError.value = e?.message || 'Could not parse request'
    setTimeout(() => { aiError.value = null }, 6000)
  } finally {
    aiLoading.value = false
  }
}

function clearAi() {
  aiFilters.value = null
  aiLabel.value = ''
  aiQuery.value = ''
  aiError.value = null
  load()
}

onMounted(load)
</script>

<style scoped>
.cat-editor {
  margin-top: 10px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
  background: var(--bg-secondary, #f8f9fa);
  border-radius: 0 0 8px 8px;
}

.cat-editor-hd {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}

.cat-editor-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.cat-select {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  background: var(--bg);
  color: var(--text);
}

.dt-detail-panel {
  margin-top: 12px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  overflow: hidden;
}

.dt-detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}

.dt-detail-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.dt-detail-body {
  padding: 16px;
}

.dt-detail-body .detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px 20px;
}

.dt-detail-body .cat-editor {
  margin-top: 16px;
  border-radius: var(--radius-sm);
}

/* ── AI AMA ──────────────────────────────────────────────────────────────── */
.ai-ama-bar { margin-top: 2px; }

.ai-ama-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  padding: 0 10px;
  border: 1.5px solid var(--primary);
  border-radius: 8px;
  background: var(--card);
  transition: opacity 0.15s;
}
.ai-ama-wrap.loading { opacity: 0.6; }

.ai-ama-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--primary);
  white-space: nowrap;
  letter-spacing: 0.04em;
}

.ai-ama-input {
  flex: 1;
  border: none;
  background: transparent;
  padding: 8px 0;
  font-size: 13px;
  color: var(--text);
  outline: none;
  min-width: 0;
}
.ai-ama-input::placeholder { color: var(--text-muted); }
.ai-ama-input:disabled { cursor: not-allowed; }

.ai-active-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0 4px;
  padding: 6px 12px;
  background: linear-gradient(90deg, rgba(15,118,110,0.07), rgba(15,118,110,0.03));
  border-left: 3px solid var(--primary);
  border-radius: 0 8px 8px 0;
  font-size: 13px;
}
.ai-active-icon { font-size: 14px; flex-shrink: 0; }
.ai-active-query { flex: 1; font-style: italic; color: var(--text-muted); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-clear-btn { margin-left: auto; flex-shrink: 0; }

.ai-error-toast {
  margin: 4px 0;
  padding: 6px 10px;
  font-size: 12px;
  word-break: break-all;
}

.filters-muted {
  opacity: 0.4;
  pointer-events: none;
  user-select: none;
}

.ai-exclude-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
  padding: 2px 10px;
  user-select: none;
}
.ai-exclude-toggle input { cursor: pointer; accent-color: var(--primary); }

.spinner-sm {
  width: 14px;
  height: 14px;
  border-width: 2px;
  flex-shrink: 0;
}
</style>
