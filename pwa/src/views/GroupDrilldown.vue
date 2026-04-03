<template>
  <div>
    <!-- Sub-page header -->
    <div class="drill-header">
      <button class="back-btn" @click="router.back()">
        <span class="back-arrow">‹</span>
      </button>
      <div class="drill-title-block">
        <div class="drill-title">{{ groupIcon }} {{ group }}</div>
        <div class="drill-subtitle">{{ monthLabel }}</div>
      </div>
    </div>

    <!-- Summary bar -->
    <div class="drill-summary card">
      <div class="ds-item">
        <div class="ds-label">Group Total</div>
        <div class="ds-value text-expense">{{ fmt(groupTotal) }}</div>
      </div>
      <div class="ds-divider"></div>
      <div class="ds-item">
        <div class="ds-label">% of Spending</div>
        <div class="ds-value text-neutral">{{ groupPct }}%</div>
      </div>
      <div class="ds-divider"></div>
      <div class="ds-item">
        <div class="ds-label">Categories</div>
        <div class="ds-value text-neutral">{{ cats.length }}</div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="!cats.length" class="empty-state">
      <div class="e-icon">📭</div>
      <div class="e-msg">No spending</div>
      <div class="e-sub">No {{ group }} expenses in {{ monthLabel }}</div>
    </div>

    <!-- Category list -->
    <div v-else class="cat-list card">
      <div
        v-for="cat in cats"
        :key="cat.category"
        class="cat-drill-row"
        @click="drillToCategory(cat)"
      >
        <div class="cdr-left">
          <span class="cdr-icon">{{ cat.icon }}</span>
          <div class="cdr-info">
            <div class="cdr-name">{{ cat.category }}</div>
            <div class="cdr-sub">{{ cat.txCount }} transaction{{ cat.txCount !== 1 ? 's' : '' }}</div>
          </div>
        </div>
        <div class="cdr-right">
          <div class="cdr-amount">{{ fmt(cat.amount) }}</div>
          <div class="cdr-pct">{{ cat.pct }}%</div>
          <span class="cdr-chevron">›</span>
        </div>
      </div>

      <!-- Mini bar chart per category (only shown when >1 category) -->
      <div class="cat-bars" v-if="cats.length > 1">
        <div
          v-for="cat in cats"
          :key="'bar-' + cat.category"
          class="cat-bar-row"
        >
          <div class="cb-label">{{ cat.icon }} {{ cat.category }}</div>
          <div class="cb-track">
            <div class="cb-fill" :style="{ width: cat.pct + '%' }"></div>
          </div>
          <div class="cb-pct">{{ cat.pct }}%</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const router = useRouter()
const route  = useRoute()
const store  = useFinanceStore()

// ── Route params ─────────────────────────────────────────────────────────────
const group          = route.query.group || ''
const year           = Number(route.query.year)  || new Date().getFullYear()
const month          = Number(route.query.month) || new Date().getMonth() + 1
const owner          = route.query.owner || ''
const totalExpense   = Number(route.query.totalExpense || 0)
// The full by_category array is passed as a JSON-encoded query param so this
// view needs no extra API call — Dashboard already has this data in memory.
const byCategoryRaw  = route.query.byCategory
  ? JSON.parse(decodeURIComponent(route.query.byCategory))
  : []

// ── Constants ─────────────────────────────────────────────────────────────────
const MONTHS_LONG = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December']

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

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(n) { return formatIDR(Math.abs(n)) }

// ── Computed ──────────────────────────────────────────────────────────────────
const monthLabel = computed(() => `${MONTHS_LONG[month - 1]} ${year}`)
const groupIcon  = computed(() => GROUP_ICONS[group] || '📁')

const EXCLUDED = new Set(['Transfer', 'Adjustment'])

// All spending categories in this group, sorted by amount desc
const cats = computed(() => {
  const rows = byCategoryRaw.filter(c => {
    if (c.amount >= 0 || EXCLUDED.has(c.category)) return false
    const meta = store.categoryMap[c.category]
    return meta?.category_group === group
  })

  // Compute group total first so we can calc per-cat pct
  const gTotal = rows.reduce((s, c) => s + Math.abs(c.amount), 0)

  return rows
    .map(c => ({
      category: c.category,
      icon:     store.categoryMap[c.category]?.icon || '📁',
      amount:   Math.abs(c.amount),
      txCount:  c.transaction_count ?? 0,
      pct:      gTotal > 0 ? Math.round((Math.abs(c.amount) / gTotal) * 100) : 0,
    }))
    .sort((a, b) => b.amount - a.amount)
})

const groupTotal = computed(() =>
  cats.value.reduce((s, c) => s + c.amount, 0)
)

const groupPct = computed(() =>
  totalExpense > 0 ? Math.round((groupTotal.value / Math.abs(totalExpense)) * 100) : 0
)

// ── Navigation ────────────────────────────────────────────────────────────────
function drillToCategory(cat) {
  router.push({
    path: '/category-drilldown',
    query: {
      category:  cat.category,
      year,
      month,
      fromGroup: encodeURIComponent(group),
      ...(owner ? { owner } : {}),
    },
  })
}
</script>

<style scoped>
/* ── Sub-page header ─────────────────────────────────────────────────────── */
.drill-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.back-btn {
  width: 38px; height: 38px;
  border-radius: 12px;
  border: 1.5px solid var(--border);
  background: var(--card);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; flex-shrink: 0;
  transition: background 0.15s;
  box-shadow: var(--shadow);
}
.back-btn:active { background: var(--primary-dim); }
.back-arrow { font-size: 20px; color: var(--primary); line-height: 1; margin-top: -1px; }

.drill-title-block { min-width: 0; }
.drill-title {
  font-size: 18px; font-weight: 800; letter-spacing: -0.4px;
  color: var(--text); display: flex; align-items: center; gap: 6px;
}
.drill-subtitle { font-size: 11px; color: var(--text-muted); font-weight: 600; margin-top: 1px; }

/* ── Summary bar ─────────────────────────────────────────────────────────── */
.drill-summary {
  display: flex; align-items: center;
  padding: 12px 16px; margin-bottom: 12px;
}
.ds-item { flex: 1; text-align: center; }
.ds-label {
  font-size: 9px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.6px; color: var(--text-muted); margin-bottom: 3px;
}
.ds-value { font-size: 15px; font-weight: 800; letter-spacing: -0.3px; }
.ds-divider { width: 1px; height: 32px; background: var(--border); margin: 0 8px; flex-shrink: 0; }

/* ── Category list card ──────────────────────────────────────────────────── */
.cat-list { padding: 0; overflow: hidden; }

.cat-drill-row {
  display: flex; align-items: center;
  padding: 13px 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--border);
  gap: 10px;
  transition: background 0.12s;
  -webkit-tap-highlight-color: transparent;
}
.cat-drill-row:last-of-type { border-bottom: none; }
.cat-drill-row:active { background: var(--primary-dim); }

.cdr-left { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
.cdr-icon { font-size: 24px; flex-shrink: 0; width: 34px; text-align: center; }
.cdr-info { min-width: 0; }
.cdr-name { font-size: 14px; font-weight: 700; color: var(--text); }
.cdr-sub  { font-size: 11px; color: var(--text-muted); margin-top: 1px; }

.cdr-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.cdr-amount { font-size: 13px; font-weight: 700; color: var(--expense); }
.cdr-pct    { font-size: 11px; font-weight: 700; color: var(--text-muted); min-width: 30px; text-align: right; }
.cdr-chevron { font-size: 16px; color: var(--text-muted); }

/* ── Mini bar chart ──────────────────────────────────────────────────────── */
.cat-bars {
  padding: 12px 16px 10px;
  border-top: 1px solid var(--border);
  background: var(--bg);
}
.cat-bar-row {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 7px;
}
.cat-bar-row:last-child { margin-bottom: 0; }
.cb-label {
  font-size: 11px; color: var(--text-muted); font-weight: 600;
  width: 130px; flex-shrink: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cb-track {
  flex: 1; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden;
}
.cb-fill {
  height: 100%; background: var(--primary); border-radius: 3px;
  transition: width 0.5s ease;
}
.cb-pct { font-size: 10px; font-weight: 700; color: var(--neutral); width: 30px; text-align: right; flex-shrink: 0; }

/* ── Empty state ─────────────────────────────────────────────────────────── */
.empty-state { text-align: center; padding: 40px 20px; }
.e-icon { font-size: 36px; margin-bottom: 8px; }
.e-msg  { font-size: 15px; font-weight: 700; color: var(--text); }
.e-sub  { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

/* ── Shared colour utilities ─────────────────────────────────────────────── */
.text-expense { color: var(--expense); }
.text-neutral { color: var(--neutral); }
</style>
