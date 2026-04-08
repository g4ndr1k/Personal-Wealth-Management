<template>
  <div>
    <!-- Month navigation -->
    <div class="month-nav" style="padding:0 16px">
      <button class="nav-btn" @click="prevMonth" :disabled="isOldestDate">‹</button>
      <span class="month-label">{{ fmtDateChip(selectedDate) || '—' }}</span>
      <button class="nav-btn" @click="nextMonth" :disabled="isNewestDate">›</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading"><div class="spinner"></div> Loading…</div>

    <!-- Error -->
    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <!-- Empty state -->
    <template v-else-if="!snap">
      <div class="card" style="margin-top:16px">
        <!-- Data exists but no snapshot for this date yet -->
        <div v-if="selectedDate && dates.length" class="empty-state" style="padding:32px 16px;text-align:center">
          <div style="font-size:40px;margin-bottom:12px">📸</div>
          <div style="font-weight:600;margin-bottom:6px">No snapshot for {{ fmtDateChip(selectedDate) }}</div>
          <div class="e-sub" style="margin-bottom:16px">
            Balances or holdings exist for this month. Generate a snapshot to view your wealth summary.
          </div>
          <button class="btn btn-primary" @click="generateSnapshot" :disabled="generating">
            {{ generating ? 'Generating…' : `Generate Snapshot for ${fmtDateChip(selectedDate)}` }}
          </button>
        </div>
        <!-- No data at all -->
        <div v-else class="empty-state" style="padding:32px 16px;text-align:center">
          <div style="font-size:40px;margin-bottom:12px">💰</div>
          <div style="font-weight:600;margin-bottom:6px">No wealth data yet</div>
          <div class="e-sub" style="margin-bottom:16px">
            Add balances and holdings in the Assets tab, then generate a snapshot.
          </div>
          <RouterLink to="/holdings" class="btn btn-primary">Go to Assets →</RouterLink>
        </div>
      </div>
    </template>

    <!-- Dashboard content -->
    <template v-else>
      <!-- Net Worth hero card -->
      <div class="nw-hero">
        <div class="nw-hero-label">Net Worth · {{ fmtDateChip(snap.snapshot_date) }}</div>
        <div class="nw-hero-value">{{ fmt(snap.net_worth_idr) }}</div>
        <div class="nw-hero-mom" :class="snap.mom_change_idr >= 0 ? 'positive' : 'negative'">
          <span>{{ snap.mom_change_idr >= 0 ? '▲' : '▼' }}</span>
          {{ fmt(Math.abs(snap.mom_change_idr)) }}
          <span class="mom-pct" v-if="prevNetWorth > 0">({{ momPct }}%)</span>
          <span class="mom-label">vs prev month</span>
        </div>
      </div>

      <!-- Assets vs Liabilities summary -->
      <div class="summary-grid" style="margin-bottom:0">
        <div class="summary-card">
          <div class="s-label">Total Assets</div>
          <div class="s-value text-income">{{ fmt(snap.total_assets_idr) }}</div>
        </div>
        <div class="summary-card">
          <div class="s-label">Total Liabilities</div>
          <div class="s-value text-expense">{{ fmt(snap.total_liabilities_idr) }}</div>
        </div>
      </div>

      <!-- Month-over-Month comparison card -->
      <div v-if="prevSnap" class="card">
        <div class="card-title" style="display:flex;align-items:center;justify-content:space-between">
          <span>Monthly Movement</span>
          <span class="mom-period-label">{{ fmtDateChip(prevSnap.snapshot_date) }} → {{ fmtDateChip(snap.snapshot_date) }}</span>
        </div>
        <div
          v-for="row in momRows"
          :key="row.label"
          class="mom-row"
          :class="row.label === 'Net Worth' ? 'mom-row-total' : ''"
        >
          <span class="mom-row-icon">{{ row.icon }}</span>
          <span class="mom-row-label">{{ row.label }}</span>
          <span class="mom-row-values">
            <span class="mom-row-prev">{{ fmtM(row.prev) }}</span>
            <span class="mom-row-arrow">→</span>
            <span class="mom-row-curr">{{ fmtM(row.curr) }}</span>
          </span>
          <span
            class="mom-row-delta"
            :class="row.isLiability
              ? (row.delta > 0 ? 'neg' : row.delta < 0 ? 'pos' : 'zero')
              : (row.delta > 0 ? 'pos' : row.delta < 0 ? 'neg' : 'zero')"
          >
            {{ row.delta > 0 ? '▲' : row.delta < 0 ? '▼' : '—' }}
            {{ row.delta !== 0 ? fmtM(Math.abs(row.delta)) : '' }}
            <span v-if="row.pct !== null && row.delta !== 0" class="mom-row-pct">{{ Math.abs(row.pct) }}%</span>
          </span>
        </div>
      </div>

      <!-- Asset breakdown by group -->
      <div class="card">
        <div class="card-title">Asset Breakdown</div>
        <div
          v-for="grp in assetGroups"
          :key="grp.label"
          class="wealth-row"
          @click="router.push({ path: '/holdings', query: { group: grp.label } })"
          role="button"
        >
          <div class="wealth-row-header">
            <span class="wealth-icon">{{ grp.icon }}</span>
            <span class="wealth-label">{{ grp.label }}</span>
            <span class="wealth-value">{{ fmt(grp.total) }}</span>
            <span class="wealth-pct">{{ grp.pct }}%</span>
            <span class="cat-drill-chevron">›</span>
          </div>
          <div class="cat-bar-bg">
            <div class="cat-bar-fill cat-bar-wealth" :style="{ width: grp.pct + '%' }"></div>
          </div>
          <div v-if="grp.subs?.length" class="grp-cats">
            <span v-for="s in grp.subs" :key="s.label" class="grp-cat-chip">
              {{ s.label }} {{ fmt(s.total) }}
            </span>
          </div>
        </div>

        <!-- Liabilities row -->
        <div
          v-if="snap.total_liabilities_idr > 0"
          class="wealth-row wealth-row-liab"
          @click="router.push({ path: '/holdings', query: { group: 'Liabilities' } })"
          role="button"
        >
          <div class="wealth-row-header">
            <span class="wealth-icon">🔴</span>
            <span class="wealth-label">Liabilities</span>
            <span class="wealth-value text-expense">{{ fmt(snap.total_liabilities_idr) }}</span>
            <span class="cat-drill-chevron">›</span>
          </div>
          <div v-if="liabSubs.length" class="grp-cats">
            <span v-for="s in liabSubs" :key="s.label" class="grp-cat-chip">
              {{ s.label }} {{ fmt(s.total) }}
            </span>
          </div>
        </div>
      </div>

      <!-- 12-month trend chart -->
      <div class="card">
        <div class="card-title">Net Worth Trend</div>
        <div v-if="explanation?.available" class="trend-explanation">
          <div class="trend-explanation-headline">{{ explanation.headline }}</div>
          <div class="trend-explanation-summary">{{ explanation.summary }}</div>
          <div v-if="explanation.drivers?.length" class="trend-driver-list">
            <div v-for="driver in explanation.drivers" :key="driver" class="trend-driver-item">
              {{ driver }}
            </div>
          </div>
          <div class="trend-ai">
            <div class="trend-ai-label">Ask AI to explain further</div>
            <div v-if="suggestedQuestions.length" class="trend-suggestion-list">
              <button
                v-for="question in suggestedQuestions"
                :key="question"
                class="trend-suggestion-chip"
                @click="askFollowUp(question)"
                :disabled="askingAi"
              >
                {{ question }}
              </button>
            </div>
            <div class="trend-ask-row">
              <input
                v-model="followUpQuestion"
                class="form-input trend-ask-input"
                placeholder="Ask about the Rp 1.7B investment increase..."
                @keydown.enter.prevent="submitFollowUp"
              />
              <button class="btn btn-primary btn-sm" @click="submitFollowUp" :disabled="askingAi || !followUpQuestion.trim()">
                {{ askingAi ? 'Asking…' : 'Ask' }}
              </button>
            </div>
            <div v-if="qaHistory.length" class="trend-qa-list">
              <div v-for="(item, idx) in qaHistory" :key="idx" class="trend-qa-item">
                <div class="trend-qa-question">{{ item.question }}</div>
                <div class="trend-qa-answer-title" v-if="item.answer?.title">{{ item.answer.title }}</div>
                <div class="trend-qa-answer">{{ item.answer?.answer }}</div>
                <div v-if="item.answer?.bullets?.length" class="trend-qa-bullets">
                  <div v-for="bullet in item.answer.bullets" :key="bullet" class="trend-qa-bullet">{{ bullet }}</div>
                </div>
                <div v-if="item.answer?.references?.length" class="trend-qa-refs">
                  Based on: {{ item.answer.references.join(', ') }}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div v-if="history.length < 2" class="empty-state" style="padding:16px 0">
          <div class="e-sub">Generate at least 2 monthly snapshots to see a trend</div>
        </div>
        <div v-else class="chart-wrap">
          <canvas ref="trendRef"></canvas>
        </div>
      </div>

      <!-- Generate / refresh snapshot -->
      <div style="padding:0 16px 16px">
        <button class="btn btn-primary" style="width:100%" @click="generateSnapshot" :disabled="generating">
          {{ generating ? 'Generating…' : `Refresh Snapshot for ${fmtDateChip(selectedDate)}` }}
        </button>
      </div>
    </template>

    <!-- FAB: go to Holdings -->
    <RouterLink v-if="!loading" to="/holdings" class="wealth-fab" title="Manage assets">
      <span>+</span>
    </RouterLink>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import Chart from 'chart.js/auto'
import { api } from '../api/client.js'
import { formatIDR } from '../utils/currency.js'

const router = useRouter()

const loading    = ref(false)
const error      = ref(null)
const generating = ref(false)

const snap        = ref(null)
const balances    = ref([])
const holdings    = ref([])
const liabilities = ref([])
const dates       = ref([])
const history     = ref([])
const explanation = ref(null)
const followUpQuestion = ref('')
const askingAi = ref(false)
const qaHistory = ref([])
const selectedDate = ref('')

const trendRef = ref(null)
let trendChart = null

// ── Format helpers ────────────────────────────────────────────────────────────
function fmt(n) { return formatIDR(n ?? 0) }

function fmtDateChip(d) {
  if (!d) return ''
  const [y, m] = d.split('-')
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${MONTHS[parseInt(m, 10) - 1]} ${y}`
}

function monthKey(d) {
  return (d || '').slice(0, 7)
}

function collapseMonthDates(dateList, preferredDate = '') {
  const seen = new Set()
  const preferredMonth = monthKey(preferredDate)
  const out = []
  for (const d of dateList || []) {
    const key = monthKey(d)
    if (!key || seen.has(key)) continue
    out.push(key === preferredMonth && preferredDate ? preferredDate : d)
    seen.add(key)
  }
  return out
}

function collapseMonthlyHistory(rows) {
  const byMonth = new Map()
  for (const row of rows || []) {
    byMonth.set(monthKey(row.snapshot_date), row)
  }
  return [...byMonth.values()].sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date))
}

// Compact millions formatter: 160739706.51 → "160.7M"
function fmtM(n) {
  if (n === null || n === undefined) return '—'
  const abs = Math.abs(n)
  if (abs === 0) return '0'
  if (abs >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1) + 'B'
  if (abs >= 1_000_000)     return (n / 1_000_000).toFixed(1) + 'M'
  if (abs >= 1_000)         return (n / 1_000).toFixed(0) + 'K'
  return String(Math.round(n))
}

// ── Computed ──────────────────────────────────────────────────────────────────
const prevNetWorth = computed(() => {
  if (!snap.value || !history.value.length) return 0
  const idx = history.value.findIndex(h => h.snapshot_date === snap.value.snapshot_date)
  return idx > 0 ? history.value[idx - 1].net_worth_idr : 0
})

const momPct = computed(() => {
  if (!snap.value || prevNetWorth.value <= 0) return '—'
  return Math.abs(Math.round((snap.value.mom_change_idr / prevNetWorth.value) * 100))
})

// Full previous snapshot object (for detailed MoM comparison).
// Uses monthKey matching so a snap date like '2026-04-04' still finds its
// position in a history that was collapsed to '2026-04-30' for April.
// An extra guard ensures we never compare a month to itself (which would
// happen if two snapshots share a calendar month).
const prevSnap = computed(() => {
  if (!snap.value || !history.value.length) return null
  const currentMonth = monthKey(snap.value.snapshot_date)
  const idx = history.value.findIndex(h => monthKey(h.snapshot_date) === currentMonth)
  if (idx <= 0) return null
  const candidate = history.value[idx - 1]
  // Guard: skip if candidate is somehow in the same calendar month
  if (monthKey(candidate.snapshot_date) === currentMonth) return null
  return candidate
})

// MoM comparison rows — only populated when prevSnap exists
const momRows = computed(() => {
  if (!snap.value) return []
  const s = snap.value
  const p = prevSnap.value || {}

  const rows = [
    {
      icon: '🏦',
      label: 'Cash & Liquid',
      prev: (p.savings_idr||0) + (p.checking_idr||0) + (p.money_market_idr||0) + (p.physical_cash_idr||0),
      curr: s.savings_idr + s.checking_idr + s.money_market_idr + s.physical_cash_idr,
      isLiability: false,
    },
    {
      icon: '📈',
      label: 'Investments',
      prev: (p.bonds_idr||0) + (p.stocks_idr||0) + (p.mutual_funds_idr||0) + (p.retirement_idr||0) + (p.crypto_idr||0),
      curr: s.bonds_idr + s.stocks_idr + s.mutual_funds_idr + s.retirement_idr + s.crypto_idr,
      isLiability: false,
    },
    {
      icon: '🏠',
      label: 'Real Estate',
      prev: p.real_estate_idr || 0,
      curr: s.real_estate_idr,
      isLiability: false,
    },
    {
      icon: '🚗',
      label: 'Physical Assets',
      prev: (p.vehicles_idr||0) + (p.gold_idr||0) + (p.other_assets_idr||0),
      curr: s.vehicles_idr + s.gold_idr + s.other_assets_idr,
      isLiability: false,
    },
    {
      icon: '🔴',
      label: 'Liabilities',
      prev: p.total_liabilities_idr || 0,
      curr: s.total_liabilities_idr,
      isLiability: true,
    },
    {
      icon: '💎',
      label: 'Net Worth',
      prev: p.net_worth_idr || 0,
      curr: s.net_worth_idr,
      isLiability: false,
    },
  ]

  return rows
    .filter(r => r.prev !== 0 || r.curr !== 0)
    .map(r => {
      const delta = r.curr - r.prev
      const pct   = r.prev !== 0 ? Math.round((delta / Math.abs(r.prev)) * 100) : null
      return { ...r, delta, pct }
    })
})

const assetGroups = computed(() => {
  if (!snap.value) return []
  const s = snap.value
  const totalAssets = s.total_assets_idr || 1
  const pct = (v) => totalAssets > 0 ? Math.round((v / totalAssets) * 100) : 0

  return [
    {
      label: 'Cash & Liquid', icon: '🏦',
      total: s.savings_idr + s.checking_idr + s.money_market_idr + s.physical_cash_idr,
      subs: [
        s.savings_idr       > 0 && { label: 'Savings',       total: s.savings_idr },
        s.checking_idr      > 0 && { label: 'Checking',      total: s.checking_idr },
        s.money_market_idr  > 0 && { label: 'Money Market',  total: s.money_market_idr },
        s.physical_cash_idr > 0 && { label: 'Physical Cash', total: s.physical_cash_idr },
      ].filter(Boolean),
    },
    {
      label: 'Investments', icon: '📈',
      total: s.bonds_idr + s.stocks_idr + s.mutual_funds_idr + s.retirement_idr + s.crypto_idr,
      subs: [
        s.bonds_idr        > 0 && { label: 'Bonds',        total: s.bonds_idr },
        s.stocks_idr       > 0 && { label: 'Stocks',       total: s.stocks_idr },
        s.mutual_funds_idr > 0 && { label: 'Mutual Funds', total: s.mutual_funds_idr },
        s.retirement_idr   > 0 && { label: 'Retirement',   total: s.retirement_idr },
        s.crypto_idr       > 0 && { label: 'Crypto',       total: s.crypto_idr },
      ].filter(Boolean),
    },
    {
      label: 'Real Estate', icon: '🏠',
      total: s.real_estate_idr,
      subs: [],
    },
    {
      label: 'Physical Assets', icon: '🚗',
      total: s.vehicles_idr + s.gold_idr + s.other_assets_idr,
      subs: [
        s.vehicles_idr     > 0 && { label: 'Vehicles', total: s.vehicles_idr },
        s.gold_idr         > 0 && { label: 'Gold',     total: s.gold_idr },
        s.other_assets_idr > 0 && { label: 'Other',    total: s.other_assets_idr },
      ].filter(Boolean),
    },
  ].filter(g => g.total > 0)
   .map(g => ({ ...g, pct: pct(g.total) }))
})

const liabSubs = computed(() => {
  if (!snap.value) return []
  const s = snap.value
  return [
    s.mortgages_idr         > 0 && { label: 'Mortgage',       total: s.mortgages_idr },
    s.personal_loans_idr    > 0 && { label: 'Personal Loans', total: s.personal_loans_idr },
    s.credit_card_debt_idr  > 0 && { label: 'Credit Cards',   total: s.credit_card_debt_idr },
    s.taxes_owed_idr        > 0 && { label: 'Taxes Owed',     total: s.taxes_owed_idr },
    s.other_liabilities_idr > 0 && { label: 'Other',          total: s.other_liabilities_idr },
  ].filter(Boolean)
})

const suggestedQuestions = computed(() => {
  if (!explanation.value?.available) return []
  const rows = explanation.value.rows || []
  const investment = rows.find(r => r.label === 'Investments')
  const cash = rows.find(r => r.label === 'Cash & Liquid')
  const physical = rows.find(r => r.label === 'Physical Assets')
  return [
    investment?.delta > 0 ? `What made Investments rise by ${fmtM(investment.delta)}?` : null,
    cash?.delta < 0 ? `Which cash accounts fell by ${fmtM(Math.abs(cash.delta))}?` : null,
    'Show the top item-level changes this month',
    physical?.delta > 0 ? `What changed inside Physical Assets by ${fmtM(physical.delta)}?` : null,
  ].filter(Boolean)
})

// ── Chart ─────────────────────────────────────────────────────────────────────
function destroyChart() {
  if (trendChart) { trendChart.destroy(); trendChart = null }
}

function buildChart() {
  if (!trendRef.value || history.value.length < 2) return
  destroyChart()
  const labels = history.value.map(h => fmtDateChip(h.snapshot_date))
  const data   = history.value.map(h => Math.round(h.net_worth_idr / 1_000_000))
  trendChart = new Chart(trendRef.value, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Net Worth (IDR M)',
        data,
        borderColor: '#1e3a5f',
        backgroundColor: 'rgba(30,58,95,0.08)',
        borderWidth: 2,
        pointRadius: 3,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => `Rp ${ctx.parsed.y.toLocaleString()} M` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 11 } } },
        y: { grid: { color: 'rgba(0,0,0,0.06)' }, ticks: { font: { size: 11 }, callback: (v) => `${v}M` } },
      },
    },
  })
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value   = null
  try {
    const [summary, hist, explanationRes] = await Promise.all([
      api.wealthSummary({ snapshot_date: selectedDate.value || undefined }),
      api.wealthHistory(24),
      api.wealthExplanation({ snapshot_date: selectedDate.value || undefined }).catch(() => null),
    ])
    const collapsedDates = collapseMonthDates(summary.dates, summary.snapshot_date || selectedDate.value)
    const collapsedHistory = collapseMonthlyHistory(hist)

    snap.value        = summary.snapshot
    balances.value    = summary.balances
    holdings.value    = summary.holdings
    liabilities.value = summary.liabilities
    dates.value       = collapsedDates
    history.value     = collapsedHistory
    explanation.value = explanationRes
    qaHistory.value = []
    followUpQuestion.value = ''

    // Auto-select most recent date (prefer a date with a snapshot, fall back to any data date)
    if (!selectedDate.value) {
      selectedDate.value = summary.snapshot_date || (collapsedDates.length ? collapsedDates[0] : '')
    } else if (!collapsedDates.includes(selectedDate.value)) {
      selectedDate.value = collapsedDates.find(d => monthKey(d) === monthKey(selectedDate.value)) || selectedDate.value
    }
    await nextTick()
    buildChart()
  } catch (e) {
    error.value = e.message
    explanation.value = null
  } finally {
    loading.value = false
  }
}

async function askFollowUp(question) {
  if (!question?.trim()) return
  askingAi.value = true
  const pending = { question, answer: { title: '', answer: 'Thinking…', bullets: [], references: [] } }
  qaHistory.value = [pending, ...qaHistory.value].slice(0, 4)
  try {
    const answer = await api.wealthExplanationQuery({
      snapshot_date: selectedDate.value || undefined,
      question,
      history: qaHistory.value.slice(1).map(item => ({
        question: item.question,
        answer: item.answer?.answer || '',
      })),
    })
    qaHistory.value[0] = { question, answer }
    qaHistory.value = [...qaHistory.value]
  } catch (e) {
    qaHistory.value[0] = {
      question,
      answer: {
        title: 'Unable to answer',
        answer: e.message || 'The AI explainer could not answer that question right now.',
        bullets: [],
        references: [],
      },
    }
    qaHistory.value = [...qaHistory.value]
  } finally {
    askingAi.value = false
  }
}

async function submitFollowUp() {
  const question = followUpQuestion.value.trim()
  if (!question) return
  followUpQuestion.value = ''
  await askFollowUp(question)
}

function selectDate(d) {
  selectedDate.value = d
  load()
}

// ── Month navigation ──────────────────────────────────────────────────────────
// dates[] is sorted DESC (newest first), so index 0 = most recent
const currentDateIndex = computed(() => dates.value.indexOf(selectedDate.value))
const isNewestDate = computed(() => currentDateIndex.value <= 0)
const isOldestDate = computed(() => currentDateIndex.value >= dates.value.length - 1 || !dates.value.length)

function prevMonth() {
  // Older month = higher index in DESC-sorted array
  const idx = currentDateIndex.value
  if (idx < dates.value.length - 1) selectDate(dates.value[idx + 1])
}
function nextMonth() {
  // Newer month = lower index
  const idx = currentDateIndex.value
  if (idx > 0) selectDate(dates.value[idx - 1])
}

async function generateSnapshot() {
  if (!selectedDate.value) return
  generating.value = true
  try {
    await api.createSnapshot({ snapshot_date: selectedDate.value })
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

// ── Lifecycle ─────────────────────────────────────────────────────────────────
onMounted(load)
onUnmounted(destroyChart)
</script>

<style scoped>
/* ── Hero net worth card ─────────────────────────────────────────────────────  */
.nw-hero {
  margin: 12px 16px 0;
  background: linear-gradient(135deg, var(--primary-deep) 0%, var(--primary) 100%);
  border-radius: var(--radius-lg);
  padding: 20px 20px 18px;
  color: #fff;
  box-shadow: var(--shadow-md);
}
.nw-hero-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.7;
  margin-bottom: 4px;
}
.nw-hero-value {
  font-size: 30px;
  font-weight: 700;
  letter-spacing: -0.5px;
  line-height: 1.1;
}
.nw-hero-mom {
  margin-top: 8px;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
}
.nw-hero-mom.positive { color: #4ade80; }
.nw-hero-mom.negative { color: #f87171; }
.mom-pct   { opacity: 0.85; font-weight: 500; }
.mom-label { opacity: 0.6; font-size: 12px; font-weight: 400; margin-left: 2px; }

/* ── Month-over-Month comparison card ───────────────────────────────────────── */
.mom-period-label {
  font-size: 11px;
  color: var(--neutral);
  font-weight: 400;
}
.mom-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.mom-row:last-child     { border-bottom: none; }
.mom-row-total          { padding-top: 11px; border-top: 1.5px solid var(--border); border-bottom: none; }
.mom-row-icon           { font-size: 15px; flex-shrink: 0; }
.mom-row-label          { flex: 1; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mom-row-values         { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
.mom-row-prev           { font-size: 12px; color: var(--neutral); }
.mom-row-arrow          { font-size: 11px; color: var(--text-muted); }
.mom-row-curr           { font-size: 13px; font-weight: 700; color: var(--text); min-width: 50px; text-align: right; }
.mom-row-delta          { min-width: 70px; text-align: right; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.mom-row-delta.pos      { color: #16a34a; }
.mom-row-delta.neg      { color: #dc2626; }
.mom-row-delta.zero     { color: var(--neutral); }
.mom-row-pct            { font-size: 10px; opacity: 0.75; margin-left: 2px; }

/* ── Wealth breakdown rows ───────────────────────────────────────────────────  */
.wealth-row {
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.12s;
}
.wealth-row:last-child  { border-bottom: none; }
.wealth-row:active      { background: var(--primary-dim); }
.wealth-row-liab        { opacity: 0.9; }
.wealth-row-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.wealth-icon  { font-size: 18px; flex-shrink: 0; }
.wealth-label { flex: 1; font-weight: 600; font-size: 14px; }
.wealth-value { font-size: 14px; font-weight: 700; color: var(--text); }
.wealth-pct   { font-size: 12px; color: var(--neutral); min-width: 34px; text-align: right; }

.cat-bar-wealth { background: var(--primary); opacity: 0.7; }

.trend-explanation {
  margin-bottom: 14px;
  padding: 12px 14px;
  border: 1px solid rgba(30, 58, 95, 0.1);
  border-radius: 14px;
  background: linear-gradient(180deg, rgba(30, 58, 95, 0.04), rgba(30, 58, 95, 0.02));
}
.trend-explanation-headline {
  font-size: 13px;
  font-weight: 700;
  color: var(--primary-deep);
  margin-bottom: 6px;
}
.trend-explanation-summary {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}
.trend-driver-list {
  margin-top: 10px;
  display: grid;
  gap: 6px;
}
.trend-driver-item {
  font-size: 12px;
  line-height: 1.45;
  color: var(--neutral);
}
.trend-driver-item::before {
  content: '•';
  color: var(--primary);
  margin-right: 8px;
}

.trend-ai {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid rgba(30, 58, 95, 0.08);
}
.trend-ai-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.trend-suggestion-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.trend-suggestion-chip {
  border: 1px solid var(--border);
  background: #fff;
  color: var(--primary-deep);
  border-radius: 999px;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.trend-suggestion-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.trend-ask-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.trend-ask-input {
  flex: 1;
  min-height: 40px;
}
.trend-qa-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}
.trend-qa-item {
  background: rgba(255,255,255,0.7);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 12px;
  padding: 12px;
}
.trend-qa-question {
  font-size: 12px;
  font-weight: 700;
  color: var(--primary-deep);
  margin-bottom: 6px;
}
.trend-qa-answer-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 4px;
}
.trend-qa-answer {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}
.trend-qa-bullets {
  display: grid;
  gap: 4px;
  margin-top: 8px;
}
.trend-qa-bullet {
  font-size: 12px;
  line-height: 1.45;
  color: var(--neutral);
}
.trend-qa-bullet::before {
  content: '•';
  color: var(--primary);
  margin-right: 8px;
}
.trend-qa-refs {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 8px;
}

/* ── Shared group/chip styles ────────────────────────────────────────────────  */
.cat-drill-chevron { font-size: 14px; color: var(--text-muted); margin-left: 2px; font-weight: 400; }
.grp-cats { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.grp-cat-chip {
  font-size: 10px; font-weight: 600;
  color: var(--neutral);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 2px 7px;
  white-space: nowrap;
}

/* ── FAB ─────────────────────────────────────────────────────────────────────  */
.wealth-fab {
  position: fixed;
  bottom: calc(var(--nav-h) + var(--safe-bottom) + 16px);
  right: max(16px, calc(50vw - 214px));
  width: 52px; height: 52px;
  border-radius: 50%;
  background: var(--primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  font-weight: 300;
  text-decoration: none;
  box-shadow: var(--shadow-md);
  z-index: 10;
  transition: background 0.15s;
}
.wealth-fab:active { background: var(--primary-deep); }
</style>
