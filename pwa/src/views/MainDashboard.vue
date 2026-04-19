<template>
  <div class="main-dashboard">
    <div v-if="loading" class="loading">
      <div class="spinner"></div> Loading dashboard…
    </div>

    <div v-else-if="error" class="alert alert-error">
      {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <template v-else>
      <div v-if="!currentSnapshot" class="card dashboard-empty">
        <div class="dashboard-empty__icon">💰</div>
        <div class="dashboard-empty__title">No dashboard data in this range</div>
        <div class="e-sub">Adjust the dashboard range in Settings or generate snapshots for the selected months.</div>
        <RouterLink to="/holdings" class="btn btn-primary dashboard-empty__cta">Go to Assets →</RouterLink>
      </div>

      <template v-else>
        <!-- ── Hero: Net Worth ──────────────────────────────────────────── -->
        <section class="dash-hero">
          <div class="dash-hero__bg"></div>
          <div class="dash-hero__inner">
            <div class="dash-hero__left">
              <span class="dash-hero__badge">Portfolio Overview</span>
              <h1 class="dash-hero__value">{{ fmt(currentSnapshot.net_worth_idr) }}</h1>
              <p class="dash-hero__sub">Net Worth &nbsp;·&nbsp; {{ fmtDate(currentSnapshot.snapshot_date) }}</p>
            </div>
            <div class="dash-hero__delta" :class="netWorthChange.value >= 0 ? 'up' : 'down'">
              <div class="dash-hero__delta-arrow">{{ netWorthChange.value >= 0 ? '↑' : '↓' }}</div>
              <div>
                <div class="dash-hero__delta-val">
                  {{ netWorthChange.value >= 0 ? '+' : '' }}{{ fmt(Math.abs(netWorthChange.value)) }}
                </div>
                <div class="dash-hero__delta-pct">
                  {{ netWorthChange.percent >= 0 ? '+' : '' }}{{ netWorthChange.percent.toFixed(1) }}%
                  <span class="dash-hero__delta-period">30 days</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- ── Allocation + Assets Over Time side by side ─────────────── -->
        <section class="dash-stack">
          <!-- Asset Allocation -->
          <article class="dash-card dash-card--alloc">
            <div class="dash-card__header">
              <div>
                <div class="dash-card__title">Asset Allocation</div>
                <div class="dash-card__sub">Current distribution from latest snapshot</div>
              </div>
            </div>
            <div v-if="allocationSeries.length > 0" class="alloc-body">
              <div class="alloc-kpis">
                <div class="dash-kpi">
                  <div class="dash-kpi__icon">🏦</div>
                  <div>
                    <div class="dash-kpi__label">Total Assets</div>
                    <div class="dash-kpi__value">{{ fmt(currentSnapshot.total_assets_idr) }}</div>
                  </div>
                </div>
                <div class="dash-kpi">
                  <div class="dash-kpi__icon">🔴</div>
                  <div>
                    <div class="dash-kpi__label">Liabilities</div>
                    <div class="dash-kpi__value">{{ fmt(currentSnapshot.total_liabilities_idr) }}</div>
                  </div>
                </div>
                <div class="dash-kpi">
                  <div class="dash-kpi__icon">📈</div>
                  <div>
                    <div class="dash-kpi__label">Income YTD</div>
                    <div class="dash-kpi__value">{{ fmt(incomeYtd) }}</div>
                  </div>
                </div>
                <div class="dash-kpi">
                  <div class="dash-kpi__icon">📉</div>
                  <div>
                    <div class="dash-kpi__label">Spending YTD</div>
                    <div class="dash-kpi__value">{{ fmt(spendingYtd) }}</div>
                  </div>
                </div>
              </div>
              <div class="alloc-donut">
                <canvas ref="allocationRef" aria-label="Asset allocation chart"></canvas>
                <div class="donut-center-overlay">
                  <div class="donut-center-label">Total</div>
                  <div class="donut-center-value">{{ fmtShort(totalAllocationValue) }}</div>
                </div>
              </div>
              <div class="alloc-legend alloc-legend--tight">
                <div v-for="slice in allocationSeries" :key="slice.label" class="alloc-legend__item">
                  <span class="alloc-legend__dot" :style="{ backgroundColor: slice.color }"></span>
                  <span class="alloc-legend__label">{{ slice.label }}</span>
                  <span class="alloc-legend__pct">{{ slice.percent.toFixed(1) }}%</span>
                  <span class="alloc-legend__val">{{ fmt(slice.value) }}</span>
                </div>
              </div>
            </div>
            <div v-else class="empty-state dash-chart-empty">
              <div class="e-sub">No holdings found in the latest snapshot.</div>
            </div>
          </article>

          <!-- Assets Over Time -->
          <article class="dash-card dash-card--wealth">
            <div class="dash-card__header">
              <div>
                <div class="dash-card__title">Assets Over Time</div>
                <div class="dash-card__sub">Monthly total assets from saved snapshots</div>
              </div>
            </div>
            <div v-if="wealthSeries.length > 0" class="dash-chart dash-chart--canvas">
              <canvas ref="wealthRef" aria-label="Wealth over time chart"></canvas>
            </div>
            <div v-else class="empty-state dash-chart-empty">
              <div class="e-sub">Generate at least one monthly snapshot to see this trend.</div>
            </div>
          </article>
        </section>

        <!-- ── Cash Flow Summary (full width) ───────────────────────────── -->
        <section class="dash-card dash-card--flows">
          <div class="dash-card__header">
            <div>
              <div class="dash-card__title">Cash Flow Summary</div>
              <div class="dash-card__sub">Monthly income vs spending across {{ store.dashboardRangeLabel }}</div>
            </div>
            <RouterLink to="/flows" class="dash-card__link">Open flows →</RouterLink>
          </div>
          <div v-if="cashFlowSeries.length > 0" class="dash-chart dash-chart--canvas">
            <canvas ref="cashFlowRef" aria-label="Cash flow summary chart"></canvas>
          </div>
          <div v-else class="empty-state dash-chart-empty">
            <div class="e-sub">No transactions were found for the recent period.</div>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import Chart from 'chart.js/auto'
import { RouterLink } from 'vue-router'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { useFmt } from '../composables/useFmt.js'

const HISTORY_LIMIT = 24
const DASHBOARD_MIN_MONTH = '2026-01'

const store = useFinanceStore()

const loading = ref(false)
const error = ref('')
const wealthHistory = ref([])
const holdingsHistory = ref([])
const monthlyFlowRows = ref([])
const allocationRef = ref(null)
const wealthRef = ref(null)
const cashFlowRef = ref(null)
let allocationChart = null
let wealthChart = null
let cashFlowChart = null

const { fmt } = useFmt()

function fmtShort(value) {
  if (store.hideNumbers) return 'Rp ••••••••'
  const abs = Math.abs(value ?? 0)
  if (abs >= 1_000_000_000) return `Rp ${(abs / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `Rp ${(abs / 1_000_000).toFixed(0)}M`
  return fmt(value ?? 0)
}

function monthKey(dateString) {
  return (dateString || '').slice(0, 7)
}

function parseDate(dateString) {
  if (!dateString) return null
  return new Date(`${dateString}T00:00:00`)
}

function formatMonthLabel(key) {
  if (!key) return ''
  const [year, month] = key.split('-')
  const monthIndex = Number(month) - 1
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[monthIndex]} ${year}`
}

function fmtDate(dateString) {
  return formatMonthLabel(monthKey(dateString))
}

function isMonthInRange(month) {
  return month >= DASHBOARD_MIN_MONTH
    && month >= store.dashboardStartMonth
    && month <= store.dashboardEndMonth
}

function monthRange(startMonth, endMonth) {
  if (!startMonth || !endMonth || startMonth > endMonth) return []
  const [startYear, startMonthNum] = startMonth.split('-').map(Number)
  const [endYear, endMonthNum] = endMonth.split('-').map(Number)
  const cursor = new Date(startYear, startMonthNum - 1, 1)
  const end = new Date(endYear, endMonthNum - 1, 1)
  const out = []

  while (cursor <= end) {
    out.push(`${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}`)
    cursor.setMonth(cursor.getMonth() + 1)
  }

  return out
}

function collapseMonthlyHistory(rows) {
  const byMonth = new Map()
  for (const row of rows || []) {
    const key = monthKey(row.snapshot_date)
    if (!isMonthInRange(key)) continue
    byMonth.set(key, row)
  }
  return [...byMonth.values()].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date))
}

function aggregateHoldingsByDate(rows) {
  const byDate = new Map()
  for (const row of rows || []) {
    const date = row.snapshot_date || ''
    if (!date) continue
    const current = byDate.get(date) || { snapshot_date: date, net_worth_idr: 0 }
    current.net_worth_idr += Number(row.market_value_idr || 0)
    byDate.set(date, current)
  }
  return [...byDate.values()].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date))
}

function formatAssetClass(assetClass) {
  return {
    bond: 'Bond',
    stock: 'Stock',
    mutual_fund: 'Mutual Fund',
    real_estate: 'Real Estate',
    retirement: 'Retirement',
    crypto: 'Crypto',
    vehicle: 'Vehicle',
    gold: 'Gold',
    other: 'Other',
  }[assetClass] || assetClass || 'Other'
}

const wealthSeries = computed(() =>
  visibleSnapshots.value.map((row) => ({
    label: formatMonthLabel(monthKey(row.snapshot_date)),
    value: Number(row.total_assets_idr || 0),
  }))
)

const visibleSnapshots = computed(() =>
  wealthHistory.value.filter((row) => isMonthInRange(monthKey(row.snapshot_date)))
)

const currentSnapshot = computed(() =>
  visibleSnapshots.value.length ? visibleSnapshots.value[visibleSnapshots.value.length - 1] : null
)

const currentSnapshotMonth = computed(() => monthKey(currentSnapshot.value?.snapshot_date))

const visibleHoldings = computed(() =>
  (holdingsHistory.value || []).filter((row) => monthKey(row.snapshot_date) === currentSnapshotMonth.value)
)

const netWorthChange = computed(() => {
  if (visibleSnapshots.value.length < 2) return { value: 0, percent: 0 }
  const current = Number(visibleSnapshots.value[visibleSnapshots.value.length - 1]?.net_worth_idr || 0)
  const previous = Number(visibleSnapshots.value[visibleSnapshots.value.length - 2]?.net_worth_idr || 0)
  const delta = current - previous
  const percent = previous > 0 ? (delta / previous) * 100 : 0
  return { value: delta, percent }
})

const allocationSeries = computed(() => {
  const snapshot = currentSnapshot.value
  if (!snapshot) return []

  const totals = [
    { key: 'cash_liquid', label: 'Cash & Liquid', value: Number(snapshot.savings_idr || 0) + Number(snapshot.checking_idr || 0) + Number(snapshot.money_market_idr || 0) + Number(snapshot.physical_cash_idr || 0) },
    { key: 'bond', label: 'Bond', value: Number(snapshot.bonds_idr || 0) },
    { key: 'stock', label: 'Stock', value: Number(snapshot.stocks_idr || 0) },
    { key: 'mutual_fund', label: 'Mutual Fund', value: Number(snapshot.mutual_funds_idr || 0) },
    { key: 'retirement', label: 'Retirement', value: Number(snapshot.retirement_idr || 0) },
    { key: 'crypto', label: 'Crypto', value: Number(snapshot.crypto_idr || 0) },
    { key: 'real_estate', label: 'Real Estate', value: Number(snapshot.real_estate_idr || 0) },
    { key: 'vehicle', label: 'Vehicle', value: Number(snapshot.vehicles_idr || 0) },
    { key: 'gold', label: 'Gold', value: Number(snapshot.gold_idr || 0) },
    { key: 'other', label: 'Other', value: Number(snapshot.other_assets_idr || 0) },
  ]

  const totalValue = Number(snapshot.total_assets_idr || 0)
  const palette = ['#14b8a6', '#3b82f6', '#f59e0b', '#8b5cf6', '#f97316', '#10b981', '#ef4444', '#64748b', '#06b6d4']

  return totals
    .map((row, index) => ({
      key: row.key,
      label: row.label,
      value: row.value,
      percent: totalValue > 0 ? (row.value / totalValue) * 100 : 0,
      color: palette[index % palette.length],
    }))
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value)
})

const cashFlowSeries = computed(() =>
  monthRange(store.dashboardStartMonth, store.dashboardEndMonth).map((key) => {
    const row = monthlyFlowRows.value.find((item) => item.key === key)
    return {
      key,
      label: formatMonthLabel(key),
      income: row?.income || 0,
      spending: row?.spending || 0,
    }
  })
)

const incomeYtd = computed(() => cashFlowSeries.value.reduce((sum, row) => sum + row.income, 0))
const spendingYtd = computed(() => cashFlowSeries.value.reduce((sum, row) => sum + row.spending, 0))
const totalAllocationValue = computed(() => Number(currentSnapshot.value?.total_assets_idr || 0))

function buildPath(points) {
  if (!points.length) return ''
  return points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ')
}

function buildYTicks(maxValue, formatter) {
  const safeMax = maxValue > 0 ? maxValue : 1
  return Array.from({ length: 4 }, (_, index) => {
    const ratio = index / 3
    const value = safeMax * (1 - ratio)
    return {
      value,
      y: 24 + (232 * ratio),
      label: formatter(value),
    }
  })
}

function fmtCompact(value) {
  if (store.hideNumbers) return '•••'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `Rp ${(value / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `Rp ${(value / 1_000_000).toFixed(0)}M`
  if (abs >= 1_000) return `Rp ${(value / 1_000).toFixed(0)}K`
  return fmt(value)
}

const wealthChartModel = computed(() => {
  const rows = wealthSeries.value
  const maxValue = Math.max(...rows.map((row) => row.value), 1)
  const barWidth = rows.length ? Math.min(48, 520 / rows.length) : 0
  const gap = rows.length > 1 ? (660 - (rows.length * barWidth)) / (rows.length - 1) : 0

  return {
    yTicks: buildYTicks(maxValue, (value) => fmtCompact(value)),
    bars: rows.map((row, index) => {
      const height = Math.max(8, (row.value / maxValue) * 232)
      return {
        label: row.label,
        x: 68 + index * (barWidth + Math.max(gap, 10)),
        y: 264 - height,
        width: barWidth,
        height,
        tooltip: `${row.label}: ${fmt(row.value)}`,
      }
    }),
  }
})

function destroyAllocationChart() {
  if (!allocationChart) return
  allocationChart.destroy()
  allocationChart = null
}

function destroyCashFlowChart() {
  if (!cashFlowChart) return
  cashFlowChart.destroy()
  cashFlowChart = null
}

function destroyWealthChart() {
  if (!wealthChart) return
  wealthChart.destroy()
  wealthChart = null
}

async function renderAllocationChart() {
  if (!allocationSeries.value.length) {
    destroyAllocationChart()
    return
  }

  await nextTick()
  const canvas = allocationRef.value
  if (!canvas) return

  destroyAllocationChart()
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  allocationChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: allocationSeries.value.map((row) => row.label),
      datasets: [{
        data: allocationSeries.value.map((row) => row.value),
        backgroundColor: allocationSeries.value.map((row) => row.color),
        borderColor: '#0b1220',
        borderWidth: 4,
        hoverOffset: 10,
        spacing: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '68%',
      animation: { duration: 550 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10, 19, 33, 0.94)',
          borderColor: 'rgba(255,255,255,0.10)',
          borderWidth: 1,
          titleColor: '#edf4ff',
          bodyColor: '#c8d6e5',
          callbacks: {
            label(context) {
              const total = context.dataset.data.reduce((sum, value) => sum + Number(value || 0), 0)
              const value = Number(context.parsed || 0)
              const pct = total > 0 ? ((value / total) * 100).toFixed(1) : '0.0'
              return `${context.label}: ${fmt(value)} (${pct}%)`
            },
          },
        },
      },
    },
  })
}

async function renderWealthChart() {
  if (!wealthSeries.value.length) {
    destroyWealthChart()
    return
  }

  await nextTick()
  const canvas = wealthRef.value
  if (!canvas) return

  destroyWealthChart()
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const barGradient = ctx.createLinearGradient(0, 0, 0, canvas.height || 320)
  barGradient.addColorStop(0, '#14b8a6')
  barGradient.addColorStop(1, 'rgba(13, 148, 136, 0.7)')

  wealthChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: wealthSeries.value.map((row) => row.label),
      datasets: [{
        label: 'Assets',
        data: wealthSeries.value.map((row) => row.value),
        backgroundColor: barGradient,
        borderRadius: 8,
        borderSkipped: false,
        maxBarThickness: 42,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 500,
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10, 19, 33, 0.94)',
          borderColor: 'rgba(255,255,255,0.10)',
          borderWidth: 1,
          titleColor: '#edf4ff',
          bodyColor: '#c8d6e5',
          callbacks: {
            label(context) {
              return `Assets: ${fmt(context.parsed.y)}`
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: '#64748b',
            font: { size: 11, weight: '600' },
          },
          border: { display: false },
        },
        y: {
          beginAtZero: true,
          ticks: {
            color: '#64748b',
            callback(value) {
              return fmtCompact(Number(value))
            },
            font: { size: 11, weight: '600' },
          },
          grid: {
            color: 'rgba(148, 163, 184, 0.08)',
            drawBorder: false,
          },
          border: { display: false },
        },
      },
    },
  })
}

async function renderCashFlowChart() {
  if (!cashFlowSeries.value.length) {
    destroyCashFlowChart()
    return
  }

  await nextTick()
  const canvas = cashFlowRef.value
  if (!canvas) return

  destroyCashFlowChart()
  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const incomeGradient = ctx.createLinearGradient(0, 0, canvas.width || 800, 0)
  incomeGradient.addColorStop(0, '#22c55e')
  incomeGradient.addColorStop(1, '#4ade80')

  const spendingGradient = ctx.createLinearGradient(0, 0, canvas.width || 800, 0)
  spendingGradient.addColorStop(0, '#ef4444')
  spendingGradient.addColorStop(1, '#f87171')

  cashFlowChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: cashFlowSeries.value.map((row) => row.label),
      datasets: [
        {
          label: 'Income',
          data: cashFlowSeries.value.map((row) => row.income),
          borderColor: incomeGradient,
          backgroundColor: '#22c55e',
          borderWidth: 3,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#22c55e',
          pointBorderColor: '#0b1220',
          pointBorderWidth: 2,
          tension: 0.36,
        },
        {
          label: 'Spending',
          data: cashFlowSeries.value.map((row) => row.spending),
          borderColor: spendingGradient,
          backgroundColor: '#ef4444',
          borderWidth: 3,
          pointRadius: 4,
          pointHoverRadius: 6,
          pointBackgroundColor: '#ef4444',
          pointBorderColor: '#0b1220',
          pointBorderWidth: 2,
          tension: 0.36,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      animation: {
        duration: 500,
      },
      plugins: {
        legend: {
          position: 'top',
          align: 'start',
          labels: {
            color: '#c8d6e5',
            usePointStyle: true,
            pointStyle: 'circle',
            boxWidth: 8,
            boxHeight: 8,
            padding: 18,
            font: {
              size: 12,
              weight: '700',
            },
          },
        },
        tooltip: {
          backgroundColor: 'rgba(10, 19, 33, 0.94)',
          borderColor: 'rgba(255,255,255,0.10)',
          borderWidth: 1,
          titleColor: '#edf4ff',
          bodyColor: '#c8d6e5',
          displayColors: true,
          callbacks: {
            label(context) {
              return `${context.dataset.label}: ${fmt(context.parsed.y)}`
            },
          },
        },
      },
      scales: {
        x: {
          grid: {
            display: false,
          },
          ticks: {
            color: '#64748b',
            font: {
              size: 11,
              weight: '600',
            },
          },
          border: {
            display: false,
          },
        },
        y: {
          beginAtZero: true,
          ticks: {
            color: '#64748b',
            callback(value) {
              return fmtCompact(Number(value))
            },
            font: {
              size: 11,
              weight: '600',
            },
          },
          grid: {
            color: 'rgba(148, 163, 184, 0.08)',
            drawBorder: false,
          },
          border: {
            display: false,
          },
        },
      },
    },
  })
}

async function load() {
  loading.value = true
  error.value = ''

  try {
    const months = monthRange(store.dashboardStartMonth, store.dashboardEndMonth)
    const monthlyFlowPromises = months.map((key) => {
      const [year, month] = key.split('-').map(Number)
      return api.summaryMonth(year, month)
        .then((row) => ({
          key,
          income: Number(row?.total_income || 0),
          spending: Math.abs(Number(row?.total_expense || 0)),
        }))
        .catch(() => ({
          key,
          income: 0,
          spending: 0,
        }))
    })

    const [summary, history, allHoldings, flows] = await Promise.all([
      api.wealthSummary(),
      api.wealthHistory(HISTORY_LIMIT),
      api.getHoldings(),
      Promise.all(monthlyFlowPromises),
    ])

    holdingsHistory.value = allHoldings || []
    wealthHistory.value = collapseMonthlyHistory(history || [])
    monthlyFlowRows.value = flows
  } catch (err) {
    error.value = err.message || 'Unable to load dashboard.'
  } finally {
    loading.value = false
    await nextTick()
    if (!error.value) {
      await renderAllocationChart()
      await renderWealthChart()
      await renderCashFlowChart()
    }
  }
}

onMounted(load)
watch(() => [store.dashboardStartMonth, store.dashboardEndMonth], load)
onUnmounted(() => {
  destroyAllocationChart()
  destroyWealthChart()
  destroyCashFlowChart()
})
</script>

<style scoped>
/* ── Root ─────────────────────────────────────────────────────────────── */
.main-dashboard {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 1320px;
}

.dashboard-empty {
  padding: 28px 20px;
  text-align: center;
}
.dashboard-empty__icon { font-size: 40px; margin-bottom: 10px; }
.dashboard-empty__title { font-size: 20px; font-weight: 800; margin-bottom: 8px; color: var(--text); }
.dashboard-empty__cta { margin-top: 18px; display: inline-flex; }

/* ── Hero ─────────────────────────────────────────────────────────────── */
.dash-hero {
  position: relative;
  border-radius: 28px;
  overflow: hidden;
  min-height: 180px;
}
.dash-hero__bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 70% 80% at 20% 40%, rgba(20, 184, 166, 0.22), transparent),
    radial-gradient(ellipse 50% 70% at 80% 30%, rgba(59, 130, 246, 0.18), transparent),
    radial-gradient(ellipse 40% 50% at 60% 90%, rgba(139, 92, 246, 0.12), transparent),
    linear-gradient(135deg, #0a1628 0%, #0e2a3f 40%, #0f766e 100%);
  z-index: 0;
}
.dash-hero__inner {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 32px 36px;
}
.dash-hero__badge {
  display: inline-block;
  padding: 4px 14px;
  border-radius: 100px;
  background: rgba(255,255,255,0.10);
  border: 1px solid rgba(255,255,255,0.14);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: rgba(255,255,255,0.72);
  backdrop-filter: blur(8px);
  margin-bottom: 14px;
}
.dash-hero__value {
  margin: 0;
  font-size: clamp(38px, 4.5vw, 56px);
  line-height: 1;
  font-weight: 800;
  letter-spacing: -0.04em;
  color: #fff;
}
.dash-hero__sub {
  margin: 10px 0 0;
  font-size: 14px;
  color: rgba(255,255,255,0.56);
  font-weight: 500;
}

.dash-hero__delta {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 20px 28px;
  border-radius: 22px;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.10);
  backdrop-filter: blur(14px);
}
.dash-hero__delta.up .dash-hero__delta-arrow { color: #4ade80; }
.dash-hero__delta.down .dash-hero__delta-arrow { color: #f87171; }
.dash-hero__delta-arrow {
  font-size: 32px;
  font-weight: 800;
  line-height: 1;
}
.dash-hero__delta-val {
  font-size: 22px;
  font-weight: 800;
  color: #fff;
}
.dash-hero__delta-pct {
  font-size: 14px;
  font-weight: 700;
  color: rgba(255,255,255,0.72);
}
.dash-hero__delta-period {
  font-weight: 500;
  color: rgba(255,255,255,0.44);
  margin-left: 4px;
}

/* ── KPI cards ────────────────────────────────────────────────────────── */
.dash-kpi {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  border-radius: 20px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.07);
  backdrop-filter: blur(12px);
  transition: background 0.2s;
}
.dash-kpi:hover {
  background: rgba(255,255,255,0.07);
}
.dash-kpi__icon {
  font-size: 26px;
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  background: rgba(255,255,255,0.06);
  flex-shrink: 0;
}
.dash-kpi__label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--text-muted);
}
.dash-kpi__value {
  margin-top: 4px;
  font-size: 19px;
  font-weight: 800;
  line-height: 1.15;
  color: var(--text);
}

.alloc-kpis {
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 8px;
  align-self: stretch;
}
.alloc-kpis .dash-kpi {
  min-height: 0;
  gap: 10px;
  padding: 12px 12px;
  border-radius: 14px;
}
.alloc-kpis .dash-kpi__icon {
  width: 30px;
  height: 30px;
  font-size: 18px;
  border-radius: 10px;
}
.alloc-kpis .dash-kpi__label {
  font-size: 9px;
  letter-spacing: 0.08em;
}
.alloc-kpis .dash-kpi__value {
  margin-top: 3px;
  font-size: 14px;
  line-height: 1.1;
}

/* ── Glass Card (shared) ──────────────────────────────────────────────── */
.dash-card {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 24px;
  padding: 24px;
  backdrop-filter: blur(12px);
}
.dash-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.dash-card__title {
  font-size: 16px;
  font-weight: 800;
  color: var(--text);
  letter-spacing: -0.01em;
}
.dash-card__sub {
  margin-top: 3px;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
}
.dash-card__link {
  flex-shrink: 0;
  color: #14b8a6;
  text-decoration: none;
  font-size: 12px;
  font-weight: 700;
  padding: 6px 14px;
  border-radius: 10px;
  background: rgba(20, 184, 166, 0.10);
  border: 1px solid rgba(20, 184, 166, 0.18);
  transition: background 0.2s;
}
.dash-card__link:hover {
  background: rgba(20, 184, 166, 0.18);
}

/* ── 2-column row (Allocation + Wealth) ────────────────────────────────── */
.dash-stack {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}

/* ── Allocation Card ──────────────────────────────────────────────────── */
.alloc-body {
  display: grid;
  grid-template-columns: minmax(200px, 240px) minmax(220px, 1fr) minmax(275px, 350px);
  align-items: center;
  gap: 22px;
}
.alloc-donut {
  position: relative;
  justify-self: center;
  width: 220px;
  height: 220px;
  filter: drop-shadow(0 4px 20px rgba(20, 184, 166, 0.18));
}
.alloc-donut canvas {
  display: block;
  width: 100% !important;
  height: 100% !important;
}
.donut-center-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.donut-center-label {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.donut-center-value {
  color: var(--text);
  font-size: 15px;
  font-weight: 800;
  margin-top: 4px;
}

.alloc-legend {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  justify-self: end;
}
.alloc-legend--tight {
  max-width: 325px;
}
.alloc-legend__item {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) 56px 112px;
  gap: 8px;
  align-items: center;
  padding: 5px 10px;
  border-radius: 10px;
  background: rgba(255,255,255,0.04);
  transition: background 0.15s;
}
.alloc-legend__item:hover {
  background: rgba(255,255,255,0.08);
}
.alloc-legend__dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  flex-shrink: 0;
}
.alloc-legend__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.alloc-legend__pct {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.alloc-legend__val {
  font-size: 11px;
  font-weight: 800;
  color: var(--text);
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* ── Charts ───────────────────────────────────────────────────────────── */
.dash-chart {
  position: relative;
  height: 300px;
}
.dash-chart--canvas {
  height: 340px;
}
.dash-chart--canvas canvas {
  display: block;
  width: 100% !important;
  height: 100% !important;
}
.dash-chart-empty {
  height: 200px;
  display: grid;
  place-items: center;
}
.chart-svg {
  display: block;
  width: 100%;
  height: 100%;
}
.chart-grid-line {
  stroke: rgba(148, 163, 184, 0.08);
  stroke-width: 1;
}
.chart-axis-label {
  fill: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
}
.chart-bar-animated {
  transition: opacity 0.2s;
}
.chart-bar-animated:hover {
  opacity: 0.85;
}
.chart-line {
  fill: none;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.chart-point {
  stroke-width: 2;
  transition: r 0.15s;
}
.chart-point:hover {
  r: 7;
}
.chart-point--income {
  fill: #22c55e;
  stroke: rgba(10, 19, 33, 0.6);
}
.chart-point--spending {
  fill: #ef4444;
  stroke: rgba(10, 19, 33, 0.6);
}

/* ── Responsive ───────────────────────────────────────────────────────── */
@media (max-width: 1100px) {
  .alloc-body {
    grid-template-columns: minmax(180px, 220px) minmax(200px, 1fr) minmax(250px, 300px);
  }
  .alloc-donut {
    width: 190px;
    height: 190px;
  }
}

@media (max-width: 1024px) {
  .alloc-legend--tight {
    max-width: 290px;
  }
}

@media (max-width: 720px) {
  .dash-hero__inner {
    flex-direction: column;
    align-items: flex-start;
    padding: 24px 20px;
  }
  .dash-hero__delta {
    align-self: stretch;
  }
  .dash-card {
    padding: 18px;
  }
  .dash-chart {
    height: 240px;
  }
  .alloc-body {
    grid-template-columns: 1fr;
    align-items: stretch;
  }
  .alloc-kpis {
    width: 100%;
  }
  .alloc-donut {
    width: 180px;
    height: 180px;
    margin: 0 auto;
  }
  .alloc-legend {
    justify-self: stretch;
  }
  .alloc-legend--tight {
    max-width: none;
  }
}

@media (max-width: 1023px) {
  .dash-kpi__label,
  .donut-center-label,
  .alloc-legend__pct,
  .dash-card__sub,
  .chart-axis-label {
    color: #57534e;
    fill: #57534e;
  }

  .dash-kpi__value,
  .dash-card__title,
  .donut-center-value,
  .alloc-legend__label,
  .alloc-legend__val,
  .dashboard-empty__title {
    color: #1c1917;
  }
}

@media (max-width: 520px) {
  .dash-hero__value {
    font-size: 32px;
  }
}
</style>
