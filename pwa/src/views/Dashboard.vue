<template>
  <div>
    <!-- Month navigation -->
    <div class="month-nav">
      <button class="nav-btn" @click="prevMonth">‹</button>
      <span class="month-label">{{ monthLabel }}</span>
      <button class="nav-btn" @click="nextMonth" :disabled="isCurrentMonth">›</button>
    </div>

    <!-- Owner filter -->
    <div class="owner-tabs">
      <button
        :class="['owner-tab', !store.selectedOwner && 'active']"
        @click="store.selectedOwner = ''"
      >All</button>
      <button
        v-for="o in store.owners"
        :key="o"
        :class="['owner-tab', store.selectedOwner === o && 'active']"
        @click="store.selectedOwner = o"
      >{{ o }}</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading">
      <div class="spinner"></div> Loading…
    </div>

    <!-- Error -->
    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <template v-else-if="summary">
      <!-- Needs review alert -->
      <div v-if="summary.needs_review > 0" class="alert alert-warning">
        ⚠️ <strong>{{ summary.needs_review }}</strong> transaction{{ summary.needs_review !== 1 ? 's' : '' }} need review.
        <RouterLink to="/review" style="margin-left:auto; font-weight:700; text-decoration:none">Review →</RouterLink>
      </div>

      <!-- Summary cards -->
      <div class="summary-grid">
        <div class="summary-card">
          <div class="s-label">Income</div>
          <div class="s-value text-income">{{ fmt(displayIncome) }}</div>
        </div>
        <div class="summary-card">
          <div class="s-label">Expense</div>
          <div class="s-value text-expense">{{ fmt(Math.abs(displayExpense)) }}</div>
        </div>
        <div class="summary-card">
          <div class="s-label">Net</div>
          <div class="s-value" :class="displayNet >= 0 ? 'text-income' : 'text-expense'">
            {{ fmt(displayNet) }}
          </div>
        </div>
        <div class="summary-card">
          <div class="s-label">Transactions</div>
          <div class="s-value text-neutral">{{ summary.transaction_count }}</div>
          <div v-if="summary.needs_review" class="s-sub">{{ summary.needs_review }} unreviewed</div>
        </div>
      </div>

      <!-- Category breakdown -->
      <div class="card">
        <div class="card-title">Spending by Category</div>
        <div v-if="!topCats.length" class="empty-state" style="padding:16px 0">
          <div class="e-sub">No expense data this month</div>
        </div>
        <div v-else>
          <div v-for="cat in topCats" :key="cat.category" class="cat-row">
            <div class="cat-header">
              <span class="cat-name">
                <span>{{ catIcon(cat.category) }}</span>
                {{ cat.category }}
              </span>
              <span>
                <span class="cat-amount">{{ fmt(Math.abs(cat.amount)) }}</span>
                <span class="cat-pct">{{ (cat.pct_of_expense || 0).toFixed(0) }}%</span>
              </span>
            </div>
            <div class="cat-bar-bg">
              <div
                class="cat-bar-fill"
                :class="{ 'over-budget': cat.budget && Math.abs(cat.total) > cat.budget }"
                :style="{ width: Math.min(cat.pct_of_expense || 0, 100) + '%' }"
              ></div>
            </div>
          </div>
          <div v-if="summary.by_category?.length > 8" style="font-size:11px;color:var(--text-muted);margin-top:6px;text-align:right">
            +{{ summary.by_category.length - 8 }} more categories
          </div>
        </div>
      </div>

      <!-- Monthly trend chart -->
      <div class="card">
        <div class="card-title">{{ store.selectedYear }} — Monthly Trend</div>
        <div v-if="!yearData" class="loading" style="padding:20px"><div class="spinner"></div></div>
        <div v-else class="chart-wrap">
          <canvas ref="trendRef"></canvas>
        </div>
      </div>

      <!-- Owner split -->
      <div v-if="summary.by_owner?.length" class="card">
        <div class="card-title">By Owner</div>
        <table class="owner-table">
          <thead>
            <tr>
              <th>Owner</th>
              <th style="text-align:right">Income</th>
              <th style="text-align:right">Expense</th>
              <th style="text-align:right">Net</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="o in summary.by_owner" :key="o.owner">
              <td>{{ o.owner }}</td>
              <td class="text-income">{{ fmt(o.income) }}</td>
              <td class="text-expense">{{ fmt(Math.abs(o.expense)) }}</td>
              <td :class="o.net >= 0 ? 'text-income' : 'text-expense'">{{ fmt(o.net) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import Chart from 'chart.js/auto'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const store = useFinanceStore()
const trendRef = ref(null)
let trendChart = null

const summary  = ref(null)
const yearData = ref(null)
const loading  = ref(false)
const error    = ref(null)

const MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
const MONTHS_LONG  = ['January','February','March','April','May','June','July','August','September','October','November','December']

// ── Computed ────────────────────────────────────────────────────────────────
const monthLabel = computed(() =>
  `${MONTHS_LONG[store.selectedMonth - 1]} ${store.selectedYear}`
)

const isCurrentMonth = computed(() => {
  const n = new Date()
  return store.selectedYear === n.getFullYear() && store.selectedMonth === n.getMonth() + 1
})

// When an owner is selected, use that owner's row from by_owner for the top cards
const ownerRow = computed(() => {
  if (!store.selectedOwner || !summary.value?.by_owner) return null
  return summary.value.by_owner.find(o => o.owner === store.selectedOwner) || null
})
const displayIncome  = computed(() => ownerRow.value ? ownerRow.value.income  : summary.value?.total_income  ?? 0)
const displayExpense = computed(() => ownerRow.value ? ownerRow.value.expense : summary.value?.total_expense ?? 0)
const displayNet     = computed(() => ownerRow.value ? ownerRow.value.net     : summary.value?.net            ?? 0)

const EXCLUDED_FROM_SPENDING = new Set(['Internal Transfer'])
const topCats = computed(() => {
  const cats = summary.value?.by_category || []
  return cats
    .filter(c => c.amount < 0 && !EXCLUDED_FROM_SPENDING.has(c.category))
    .sort((a, b) => a.amount - b.amount) // most negative first
    .slice(0, 8)
})

// ── Formatters ───────────────────────────────────────────────────────────────
function fmt(n) {
  return formatIDR(n)
}

function fmtShort(n) {
  return formatIDR(n)
}

function catIcon(name) {
  return store.categoryMap[name]?.icon || '📁'
}

// ── Navigation ───────────────────────────────────────────────────────────────
function prevMonth() {
  if (store.selectedMonth === 1) { store.selectedMonth = 12; store.selectedYear-- }
  else store.selectedMonth--
}
function nextMonth() {
  if (isCurrentMonth.value) return
  if (store.selectedMonth === 12) { store.selectedMonth = 1; store.selectedYear++ }
  else store.selectedMonth++
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value   = null
  try {
    const [s, y] = await Promise.all([
      api.summaryMonth(store.selectedYear, store.selectedMonth),
      api.summaryYear(store.selectedYear),
    ])
    summary.value  = s
    yearData.value = y
    await nextTick()
    renderChart()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!yearData.value || !trendRef.value) return
  if (trendChart) { trendChart.destroy(); trendChart = null }

  const months = yearData.value.by_month || []
  const labels = months.map(m => MONTHS_SHORT[m.month - 1])

  trendChart = new Chart(trendRef.value, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Income',
          data: months.map(m => m.income),
          backgroundColor: 'rgba(34,197,94,0.7)',
          borderRadius: 3,
        },
        {
          label: 'Expense',
          data: months.map(m => Math.abs(m.expense)),
          backgroundColor: 'rgba(239,68,68,0.7)',
          borderRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10, padding: 10 } },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${fmt(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        y: {
          ticks: { callback: v => fmtShort(v), font: { size: 9 }, maxTicksLimit: 5 },
          grid: { color: 'rgba(0,0,0,0.04)' },
        },
        x: {
          ticks: { font: { size: 10 } },
          grid: { display: false },
        },
      },
    },
  })
}

// Re-render chart when year changes but summary already loaded
watch(() => store.selectedYear, async () => {
  await nextTick()
  renderChart()
})

onMounted(load)
watch([() => store.selectedYear, () => store.selectedMonth], load)
onUnmounted(() => { if (trendChart) trendChart.destroy() })
</script>
