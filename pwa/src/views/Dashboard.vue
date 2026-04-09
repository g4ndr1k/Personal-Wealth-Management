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

      <!-- Spending by Group -->
      <div class="card">
        <div class="card-title">Spending by Group</div>
        <div v-if="!spendingGroups.length" class="empty-state" style="padding:16px 0">
          <div class="e-sub">No expense data this month</div>
        </div>
        <div v-else>
          <div
            v-for="grp in spendingGroups"
            :key="grp.group"
            class="cat-row cat-row-tappable"
            @click="drillToGroup(grp)"
            role="button"
            :aria-label="`View ${grp.group} spending`"
          >
            <div class="cat-header">
              <span class="cat-name">
                <span>{{ grp.icon }}</span>
                {{ grp.group }}
              </span>
              <span style="display:flex;align-items:center;gap:4px">
                <span class="cat-amount">{{ fmt(grp.total) }}</span>
                <span class="cat-pct">{{ grp.pct }}%</span>
                <span class="cat-drill-chevron">›</span>
              </span>
            </div>
            <div class="cat-bar-bg">
              <div
                class="cat-bar-fill"
                :style="{ width: grp.pct + '%' }"
              ></div>
            </div>
            <!-- Category chips -->
            <div class="grp-cats">
              <span
                v-for="c in grp.topCats"
                :key="c"
                class="grp-cat-chip"
              >{{ catIcon(c) }} {{ c }}</span>
              <span v-if="grp.moreCats > 0" class="grp-cat-chip grp-cat-more">
                +{{ grp.moreCats }} more
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Monthly trend chart -->
      <div class="card">
        <div class="card-title">{{ store.selectedYear }} — Monthly Trend</div>
        <div v-if="trendExplanation?.available" class="trend-explanation">
          <div class="trend-explanation-topline">
            <div class="trend-explanation-headline">{{ trendExplanation.headline }}</div>
            <div v-if="trendExplanationLoading" class="trend-explanation-status">
              <span class="spinner spinner-sm"></span>
              Refining with AI…
            </div>
          </div>
          <div class="trend-explanation-summary">{{ trendExplanation.summary }}</div>
          <div v-if="trendExplanation.drivers?.length" class="trend-driver-list">
            <div v-for="driver in trendExplanation.drivers" :key="driver" class="trend-driver-item">
              {{ driver }}
            </div>
          </div>
        </div>
        <div v-else-if="trendExplanationLoading" class="trend-explanation-loading">
          <span class="spinner spinner-sm"></span>
          Building monthly trend analysis…
        </div>
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
import { useRouter } from 'vue-router'
import Chart from 'chart.js/auto'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const router = useRouter()
const store  = useFinanceStore()
const trendRef = ref(null)
let trendChart = null

const summary  = ref(null)
const yearData = ref(null)
const loading  = ref(false)
const error    = ref(null)
const trendExplanation = ref(null)
const trendExplanationLoading = ref(false)
let loadToken = 0

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

const EXCLUDED_FROM_SPENDING = new Set(['Transfer', 'Adjustment'])

// Icons for each group (mirrors GroupDrilldown.vue)
const GROUP_ICONS = {
  'Housing & Bills':      '🏠',
  'Food & Dining':        '🍽️',
  'Transportation':       '🚗',
  'Lifestyle & Personal': '🛍️',
  'Health & Family':      '❤️',
  'Travel':               '✈️',
  'Financial & Legal':    '⚖️',
  'System / Tracking':    '🔧',
}

// Roll up by_category into groups, exclude system cats, sort by total desc
const spendingGroups = computed(() => {
  const cats = summary.value?.by_category || []
  const totalExpense = Math.abs(summary.value?.total_expense ?? 0)

  // Aggregate per group
  const map = {}
  for (const c of cats) {
    if (c.amount >= 0 || EXCLUDED_FROM_SPENDING.has(c.category)) continue
    const meta  = store.categoryMap[c.category]
    const grp   = meta?.category_group || 'Other'
    if (grp === 'System / Tracking') continue
    if (!map[grp]) map[grp] = { group: grp, total: 0, cats: [] }
    map[grp].total += Math.abs(c.amount)
    map[grp].cats.push(c.category)
  }

  return Object.values(map)
    .sort((a, b) => b.total - a.total)
    .map(g => ({
      group:    g.group,
      icon:     GROUP_ICONS[g.group] || '📁',
      total:    g.total,
      pct:      totalExpense > 0 ? Math.round((g.total / totalExpense) * 100) : 0,
      topCats:  g.cats.slice(0, 3),
      moreCats: Math.max(0, g.cats.length - 3),
    }))
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

// ── Group drill-down (Level 1) ───────────────────────────────────────────────
function drillToGroup(grp) {
  const cats        = summary.value?.by_category || []
  const totalExpense = Math.abs(summary.value?.total_expense ?? 0)
  router.push({
    path: '/group-drilldown',
    query: {
      group:         grp.group,
      year:          store.selectedYear,
      month:         store.selectedMonth,
      totalExpense,
      byCategory:    encodeURIComponent(JSON.stringify(cats)),
      ...(store.selectedOwner ? { owner: store.selectedOwner } : {}),
    },
  })
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
  const token = ++loadToken
  loading.value = true
  error.value   = null
  trendExplanation.value = null
  trendExplanationLoading.value = true
  try {
    const [s, y] = await Promise.all([
      api.summaryMonth(store.selectedYear, store.selectedMonth),
      api.summaryYear(store.selectedYear),
    ])
    if (token !== loadToken) return
    summary.value  = s
    yearData.value = y
    await nextTick()
    renderChart()
  } catch (e) {
    if (token !== loadToken) return
    error.value = e.message
    trendExplanationLoading.value = false
    return
  } finally {
    if (token === loadToken) loading.value = false
  }

  api.summaryExplanation(store.selectedYear, store.selectedMonth)
    .then(res => {
      if (token !== loadToken) return
      trendExplanation.value = res
      return api.summaryExplanation(store.selectedYear, store.selectedMonth, { ai: true })
    })
    .then(res => {
      if (!res || token !== loadToken) return
      trendExplanation.value = res
    })
    .catch(() => {})
    .finally(() => {
      if (token === loadToken) trendExplanationLoading.value = false
    })
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

<style scoped>
/* Make category rows tappable */
.cat-row-tappable {
  cursor: pointer;
  border-radius: 8px;
  padding: 6px 8px;
  margin: 0 -8px 3px;
  transition: background 0.12s;
  -webkit-tap-highlight-color: transparent;
}
.cat-row-tappable:hover  { background: var(--primary-dim); }
.cat-row-tappable:active { background: rgba(30,58,95,0.14); }

.cat-drill-chevron {
  font-size: 14px;
  color: var(--text-muted);
  margin-left: 2px;
  font-weight: 400;
}

/* Category chips below each group bar */
.grp-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}
.grp-cat-chip {
  font-size: 10px;
  font-weight: 600;
  color: var(--neutral);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 7px;
  white-space: nowrap;
}
.grp-cat-more {
  color: var(--text-muted);
  font-style: italic;
}

.trend-explanation-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0 12px;
  color: var(--text-muted);
  font-size: 13px;
}

.trend-explanation {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(30,58,95,0.04), rgba(255,255,255,0.9));
}

.trend-explanation-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.trend-explanation-headline {
  font-weight: 800;
  color: var(--primary);
}

.trend-explanation-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}

.trend-explanation-summary {
  color: var(--neutral);
  line-height: 1.45;
}

.trend-driver-list {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.trend-driver-item {
  position: relative;
  padding-left: 14px;
  color: var(--neutral);
  font-size: 13px;
  line-height: 1.35;
}

.trend-driver-item::before {
  content: '•';
  position: absolute;
  left: 0;
  color: var(--primary);
  font-weight: 700;
}
</style>
