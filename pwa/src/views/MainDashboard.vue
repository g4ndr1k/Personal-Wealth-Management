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
        <section class="dashboard-hero">
          <div class="dashboard-hero__copy">
            <div class="dashboard-hero__eyebrow">Main Dashboard</div>
            <div class="dashboard-hero__value">{{ fmt(currentSnapshot.net_worth_idr) }}</div>
            <div class="dashboard-hero__subline">
              <span>Total Net Worth</span>
              <span>•</span>
              <span>{{ fmtDate(currentSnapshot.snapshot_date) }}</span>
            </div>
          </div>

          <div class="dashboard-hero__change" :class="netWorthChange.value >= 0 ? 'positive' : 'negative'">
            <div class="dashboard-hero__change-label">30D Change</div>
            <div class="dashboard-hero__change-value">
              {{ netWorthChange.value >= 0 ? '+' : '-' }}{{ fmt(Math.abs(netWorthChange.value)) }}
            </div>
            <div class="dashboard-hero__change-pct">
              {{ netWorthChange.percent >= 0 ? '+' : '' }}{{ netWorthChange.percent.toFixed(1) }}%
            </div>
          </div>
        </section>

        <section class="dashboard-kpis">
          <div class="dashboard-kpi">
            <div class="dashboard-kpi__label">Assets</div>
            <div class="dashboard-kpi__value">{{ fmt(currentSnapshot.total_assets_idr) }}</div>
          </div>
          <div class="dashboard-kpi">
            <div class="dashboard-kpi__label">Liabilities</div>
            <div class="dashboard-kpi__value">{{ fmt(currentSnapshot.total_liabilities_idr) }}</div>
          </div>
          <div class="dashboard-kpi">
            <div class="dashboard-kpi__label">Income YTD</div>
            <div class="dashboard-kpi__value">{{ fmt(incomeYtd) }}</div>
          </div>
          <div class="dashboard-kpi">
            <div class="dashboard-kpi__label">Spending YTD</div>
            <div class="dashboard-kpi__value">{{ fmt(spendingYtd) }}</div>
          </div>
        </section>

        <section class="dashboard-grid">
          <article class="card dashboard-panel dashboard-panel--wealth">
            <div class="dashboard-panel__head">
              <div>
                <div class="card-title">Wealth Over Time</div>
                <div class="dashboard-panel__subtitle">Monthly total net worth from saved snapshots</div>
              </div>
            </div>
            <div v-if="wealthSeries.length > 0" class="dashboard-chart">
              <svg class="chart-svg" viewBox="0 0 760 320" preserveAspectRatio="none" aria-label="Wealth over time chart">
                <line
                  v-for="tick in wealthChartModel.yTicks"
                  :key="`wealth-grid-${tick.value}`"
                  :x1="60"
                  :x2="730"
                  :y1="tick.y"
                  :y2="tick.y"
                  class="chart-grid-line"
                />
                <text
                  v-for="tick in wealthChartModel.yTicks"
                  :key="`wealth-label-${tick.value}`"
                  x="52"
                  :y="tick.y + 4"
                  text-anchor="end"
                  class="chart-axis-label"
                >
                  {{ tick.label }}
                </text>
                <rect
                  v-for="bar in wealthChartModel.bars"
                  :key="bar.label"
                  :x="bar.x"
                  :y="bar.y"
                  :width="bar.width"
                  :height="bar.height"
                  rx="10"
                  class="chart-bar"
                >
                  <title>{{ bar.tooltip }}</title>
                </rect>
                <text
                  v-for="bar in wealthChartModel.bars"
                  :key="`wealth-x-${bar.label}`"
                  :x="bar.x + (bar.width / 2)"
                  y="300"
                  text-anchor="middle"
                  class="chart-axis-label"
                >
                  {{ bar.label }}
                </text>
              </svg>
            </div>
            <div v-else class="empty-state dashboard-chart-empty">
              <div class="e-sub">Generate at least one monthly snapshot to see this trend.</div>
            </div>
          </article>

          <article class="card dashboard-panel dashboard-panel--allocation">
            <div class="dashboard-panel__head">
              <div>
                <div class="card-title">Asset Allocation</div>
                <div class="dashboard-panel__subtitle">Current distribution from the latest holdings snapshot</div>
              </div>
            </div>
            <div v-if="allocationSeries.length > 0" class="allocation-layout">
              <div class="allocation-chart">
                <svg class="chart-svg" viewBox="0 0 220 220" aria-label="Asset allocation chart">
                  <circle cx="110" cy="110" r="72" class="donut-track" />
                  <circle
                    v-for="slice in allocationChartModel"
                    :key="slice.key"
                    cx="110"
                    cy="110"
                    r="72"
                    fill="none"
                    :stroke="slice.color"
                    stroke-width="28"
                    :stroke-dasharray="slice.dasharray"
                    :stroke-dashoffset="slice.dashoffset"
                    stroke-linecap="butt"
                    transform="rotate(-90 110 110)"
                  >
                    <title>{{ slice.tooltip }}</title>
                  </circle>
                  <text x="110" y="102" text-anchor="middle" class="donut-center-label">Assets</text>
                  <text x="110" y="128" text-anchor="middle" class="donut-center-value">{{ fmt(totalAllocationValue) }}</text>
                </svg>
              </div>
              <div class="allocation-list">
                <div v-for="slice in allocationSeries" :key="slice.label" class="allocation-item">
                  <span class="allocation-item__swatch" :style="{ backgroundColor: slice.color }"></span>
                  <div class="allocation-item__meta">
                    <span class="allocation-item__label">{{ slice.label }}</span>
                    <span class="allocation-item__pct">{{ slice.percent.toFixed(1) }}%</span>
                  </div>
                  <div class="allocation-item__value">{{ fmt(slice.value) }}</div>
                </div>
              </div>
            </div>
            <div v-else class="empty-state dashboard-chart-empty">
              <div class="e-sub">No holdings were found in the latest snapshot.</div>
            </div>
          </article>

          <article class="card dashboard-panel dashboard-panel--flows">
            <div class="dashboard-panel__head">
              <div>
                <div class="card-title">Cash Flow Summary</div>
                <div class="dashboard-panel__subtitle">Monthly income vs spending across {{ store.dashboardRangeLabel }}</div>
              </div>
              <RouterLink to="/flows" class="dashboard-panel__link">Open flows →</RouterLink>
            </div>
            <div v-if="cashFlowSeries.length > 0" class="dashboard-chart">
              <svg class="chart-svg" viewBox="0 0 760 320" preserveAspectRatio="none" aria-label="Cash flow summary chart">
                <line
                  v-for="tick in cashFlowChartModel.yTicks"
                  :key="`cash-grid-${tick.value}`"
                  :x1="60"
                  :x2="730"
                  :y1="tick.y"
                  :y2="tick.y"
                  class="chart-grid-line"
                />
                <text
                  v-for="tick in cashFlowChartModel.yTicks"
                  :key="`cash-label-${tick.value}`"
                  x="52"
                  :y="tick.y + 4"
                  text-anchor="end"
                  class="chart-axis-label"
                >
                  {{ tick.label }}
                </text>
                <path :d="cashFlowChartModel.incomePath" class="chart-line chart-line--income" />
                <path :d="cashFlowChartModel.spendingPath" class="chart-line chart-line--spending" />
                <g v-for="point in cashFlowChartModel.incomePoints" :key="`inc-${point.label}`">
                  <circle :cx="point.x" :cy="point.y" r="4" class="chart-point chart-point--income">
                    <title>{{ point.tooltip }}</title>
                  </circle>
                </g>
                <g v-for="point in cashFlowChartModel.spendingPoints" :key="`spend-${point.label}`">
                  <circle :cx="point.x" :cy="point.y" r="4" class="chart-point chart-point--spending">
                    <title>{{ point.tooltip }}</title>
                  </circle>
                </g>
                <text
                  v-for="point in cashFlowChartModel.labels"
                  :key="`cash-x-${point.label}`"
                  :x="point.x"
                  y="300"
                  text-anchor="middle"
                  class="chart-axis-label"
                >
                  {{ point.label }}
                </text>
              </svg>
            </div>
            <div v-else class="empty-state dashboard-chart-empty">
              <div class="e-sub">No transactions were found for the recent period.</div>
            </div>
          </article>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const HISTORY_LIMIT = 24
const DASHBOARD_MIN_MONTH = '2026-01'

const store = useFinanceStore()

const loading = ref(false)
const error = ref('')
const wealthHistory = ref([])
const holdingsHistory = ref([])
const monthlyFlowRows = ref([])

function fmt(value) {
  return formatIDR(value ?? 0)
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
  collapseMonthlyHistory(aggregateHoldingsByDate(holdingsHistory.value)).map((row) => ({
    label: formatMonthLabel(monthKey(row.snapshot_date)),
    value: Number(row.net_worth_idr || 0),
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
  const totals = new Map()
  for (const row of visibleHoldings.value || []) {
    const key = row.asset_class || 'other'
    totals.set(key, (totals.get(key) || 0) + Number(row.market_value_idr || 0))
  }

  const totalValue = [...totals.values()].reduce((sum, value) => sum + value, 0)
  const palette = ['#0f766e', '#2563eb', '#f59e0b', '#7c3aed', '#ea580c', '#059669', '#dc2626', '#64748b', '#0891b2']

  return [...totals.entries()]
    .map(([assetClass, value], index) => ({
      key: assetClass,
      label: formatAssetClass(assetClass),
      value,
      percent: totalValue > 0 ? (value / totalValue) * 100 : 0,
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
const totalAllocationValue = computed(() => allocationSeries.value.reduce((sum, row) => sum + row.value, 0))

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

const allocationChartModel = computed(() => {
  const circumference = 2 * Math.PI * 72
  let offset = 0
  return allocationSeries.value.map((row) => {
    const length = circumference * (row.percent / 100)
    const slice = {
      ...row,
      dasharray: `${length} ${circumference - length}`,
      dashoffset: -offset,
      tooltip: `${row.label}: ${fmt(row.value)} (${row.percent.toFixed(1)}%)`,
    }
    offset += length
    return slice
  })
})

const cashFlowChartModel = computed(() => {
  const rows = cashFlowSeries.value
  const maxValue = Math.max(
    ...rows.flatMap((row) => [row.income, row.spending]),
    1
  )
  const step = rows.length > 1 ? 660 / (rows.length - 1) : 0
  const mapPoint = (value, index) => ({
    x: 68 + (index * step),
    y: 264 - ((value / maxValue) * 232),
  })
  const incomePoints = rows.map((row, index) => ({
    label: row.label,
    ...mapPoint(row.income, index),
    tooltip: `${row.label} Income: ${fmt(row.income)}`,
  }))
  const spendingPoints = rows.map((row, index) => ({
    label: row.label,
    ...mapPoint(row.spending, index),
    tooltip: `${row.label} Spending: ${fmt(row.spending)}`,
  }))

  return {
    yTicks: buildYTicks(maxValue, (value) => fmtCompact(value)),
    incomePoints,
    spendingPoints,
    incomePath: buildPath(incomePoints),
    spendingPath: buildPath(spendingPoints),
    labels: rows.map((row, index) => ({ label: row.label, x: 68 + (index * step) })),
  }
})

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
  }
}

onMounted(load)
watch(() => [store.dashboardStartMonth, store.dashboardEndMonth], load)
</script>

<style scoped>
.main-dashboard {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dashboard-empty {
  padding: 28px 20px;
  text-align: center;
}

.dashboard-empty__icon {
  font-size: 40px;
  margin-bottom: 10px;
}

.dashboard-empty__title {
  font-size: 20px;
  font-weight: 800;
  color: #0f172a;
  margin-bottom: 8px;
}

.dashboard-empty__cta {
  margin-top: 18px;
  display: inline-flex;
}

.dashboard-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(220px, 0.9fr);
  gap: 14px;
  padding: 22px;
  border-radius: 26px;
  background:
    radial-gradient(circle at top right, rgba(34, 197, 94, 0.14), transparent 28%),
    linear-gradient(135deg, #0f172a 0%, #13315c 52%, #0f766e 100%);
  color: #fff;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.18);
}

.dashboard-hero__eyebrow {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(255,255,255,0.66);
}

.dashboard-hero__value {
  margin-top: 8px;
  font-size: clamp(34px, 5vw, 52px);
  line-height: 1.02;
  font-weight: 800;
  letter-spacing: -0.05em;
}

.dashboard-hero__subline {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  color: rgba(255,255,255,0.74);
  font-size: 13px;
}

.dashboard-hero__change {
  align-self: stretch;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 18px;
  border-radius: 20px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.12);
  backdrop-filter: blur(10px);
}

.dashboard-hero__change.positive {
  box-shadow: inset 0 0 0 1px rgba(34, 197, 94, 0.12);
}

.dashboard-hero__change.negative {
  box-shadow: inset 0 0 0 1px rgba(248, 113, 113, 0.12);
}

.dashboard-hero__change-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: rgba(255,255,255,0.66);
}

.dashboard-hero__change-value {
  margin-top: 8px;
  font-size: 24px;
  font-weight: 800;
}

.dashboard-hero__change-pct {
  margin-top: 4px;
  font-size: 14px;
  font-weight: 700;
  color: rgba(255,255,255,0.86);
}

.dashboard-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.dashboard-kpi {
  padding: 16px 18px;
  border-radius: 18px;
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(226,232,240,0.9);
  box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
}

.dashboard-kpi__label {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
}

.dashboard-kpi__value {
  margin-top: 8px;
  font-size: 21px;
  font-weight: 800;
  line-height: 1.1;
  color: #0f172a;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(320px, 0.95fr);
  gap: 14px;
}

.dashboard-panel {
  margin-bottom: 0;
  padding: 18px;
}

.dashboard-panel--flows {
  grid-column: 1 / -1;
}

.dashboard-panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.dashboard-panel__subtitle {
  color: #64748b;
  font-size: 13px;
}

.dashboard-panel__link {
  flex-shrink: 0;
  color: #0f766e;
  text-decoration: none;
  font-size: 12px;
  font-weight: 700;
}

.dashboard-chart {
  position: relative;
  height: 320px;
}

.chart-svg {
  display: block;
  width: 100%;
  height: 100%;
}

.chart-grid-line {
  stroke: rgba(148, 163, 184, 0.2);
  stroke-width: 1;
}

.chart-axis-label {
  fill: #7c8ca5;
  font-size: 11px;
  font-weight: 600;
}

.chart-bar {
  fill: rgba(20, 184, 166, 0.82);
}

.donut-track {
  fill: none;
  stroke: rgba(148, 163, 184, 0.12);
  stroke-width: 28;
}

.donut-center-label {
  fill: #7c8ca5;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.donut-center-value {
  fill: #e2e8f0;
  font-size: 12px;
  font-weight: 800;
}

.chart-line {
  fill: none;
  stroke-width: 3;
}

.chart-line--income {
  stroke: #22c55e;
}

.chart-line--spending {
  stroke: #ef4444;
}

.chart-point {
  stroke: #0f172a;
  stroke-width: 2;
}

.chart-point--income {
  fill: #22c55e;
}

.chart-point--spending {
  fill: #ef4444;
}

.dashboard-chart-empty {
  height: 220px;
  display: grid;
  place-items: center;
}

.allocation-layout {
  display: grid;
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr);
  gap: 16px;
  align-items: center;
}

.allocation-chart {
  position: relative;
  height: 260px;
  width: 100%;
}

.allocation-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.allocation-item {
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 14px;
  background: #f8fafc;
}

.allocation-item__swatch {
  width: 12px;
  height: 12px;
  border-radius: 999px;
}

.allocation-item__meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}

.allocation-item__label {
  font-weight: 700;
  color: #0f172a;
}

.allocation-item__pct {
  color: #64748b;
  font-size: 12px;
  font-weight: 700;
}

.allocation-item__value {
  color: #0f172a;
  font-weight: 800;
  font-size: 12px;
}

@media (max-width: 1024px) {
  .dashboard-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dashboard-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .dashboard-hero {
    grid-template-columns: 1fr;
    padding: 20px 18px;
  }

  .allocation-layout {
    grid-template-columns: 1fr;
  }

  .allocation-chart,
  .dashboard-chart {
    height: 240px;
  }
}

@media (max-width: 520px) {
  .dashboard-kpis {
    grid-template-columns: 1fr;
  }

  .dashboard-panel {
    padding: 16px;
  }

  .dashboard-hero__change-value {
    font-size: 20px;
  }
}
</style>
