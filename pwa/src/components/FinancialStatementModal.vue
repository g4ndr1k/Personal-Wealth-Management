<script setup>
import { ref, watch, computed } from 'vue'
import { api } from '../api/client.js'
import { formatIDR } from '../utils/currency.js'
import { CATEGORY_SVGS } from '../utils/icons.js'

function catIcon(category) {
  return CATEGORY_SVGS[category] || ''
}

const props = defineProps({
  open:  { type: Boolean, default: false },
  start: { type: String,  required: true },
  end:   { type: String,  required: true },
})
const emit = defineEmits(['close'])

const state = ref({ loading: false, error: null, data: null })

async function load() {
  if (!props.start || !props.end) {
    state.value = { loading: false, error: 'Dashboard range is incomplete.', data: null }
    return
  }
  state.value = { loading: true, error: null, data: null }
  try {
    const data = await api.financialStatement(
      { start_month: props.start, end_month: props.end },
      { cacheMaxAgeMs: 0 },
    )
    state.value = { loading: false, error: null, data }
  } catch (e) {
    state.value = { loading: false, error: e?.message || String(e), data: null }
  }
}

watch(
  () => [props.open, props.start, props.end],
  ([open]) => { if (open) load() },
  { immediate: true },
)

const generatedAt = computed(() => {
  const ts = state.value.data?.generated_at
  if (!ts) return ''
  try { return new Date(ts).toLocaleString() } catch { return ts }
})

function close() { emit('close') }
function printNow() { window.print() }
function retry() { load() }

function fmt(value) { return formatIDR(value) }
function fmtSigned(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  const n = Number(value)
  const sign = n < 0 ? '-' : (n > 0 ? '+' : '')
  return `${sign}${formatIDR(Math.abs(n))}`
}
</script>

<template>
  <div v-if="open" class="fs-modal-overlay" @click.self="close">
    <div class="fs-modal-sheet" role="dialog" aria-labelledby="fs-modal-title">
      <div class="fs-modal-header no-print">
        <span id="fs-modal-title" class="modal-title">Financial Statement</span>
        <button class="btn btn-ghost" @click="printNow" :disabled="!state.data">Print / Save PDF</button>
        <button class="modal-close" @click="close" aria-label="Close">✕</button>
      </div>

      <div class="fs-modal-body">
        <div v-if="state.loading" class="loading">
          <span class="spinner"></span>
          <span>Generating statement…</span>
        </div>

        <div v-else-if="state.error" class="alert alert-error">
          <div><strong>Could not generate statement.</strong></div>
          <div style="margin-top:4px">{{ state.error }}</div>
          <button class="btn" style="margin-top:10px" @click="retry">Retry</button>
        </div>

        <template v-else-if="state.data">
          <header class="fs-report-head">
            <div class="fs-report-title">Personal Financial Statement</div>
            <div class="fs-report-sub">
              {{ state.data.range.label }}
              <span v-if="state.data.owner"> · Owner: {{ state.data.owner }}</span>
            </div>
            <div class="fs-report-meta">Generated {{ generatedAt }}</div>
          </header>

          <div v-if="state.data.warnings && state.data.warnings.length" class="alert alert-warn">
            <strong>Warnings</strong>
            <ul style="margin:6px 0 0 18px; padding:0">
              <li v-for="(w, i) in state.data.warnings" :key="i">{{ w }}</li>
            </ul>
          </div>

          <!-- 1. Statement of Net Worth -->
          <section class="fs-section">
            <h3 class="fs-section-title">1. Statement of Net Worth</h3>
            <table class="fs-table fs-table-compact">
              <thead>
                <tr>
                  <th></th>
                  <th class="num">Opening<br><small>{{ state.data.net_worth.opening?.snapshot_date || '—' }}</small></th>
                  <th class="num">Closing<br><small>{{ state.data.net_worth.closing?.snapshot_date || '—' }}</small></th>
                  <th class="num">Change</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Total Assets</td>
                  <td class="num">{{ state.data.net_worth.opening ? fmt(state.data.net_worth.opening.total_assets_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.closing ? fmt(state.data.net_worth.closing.total_assets_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.opening && state.data.net_worth.closing
                      ? fmtSigned(state.data.net_worth.closing.total_assets_idr - state.data.net_worth.opening.total_assets_idr)
                      : '—' }}</td>
                </tr>
                <tr>
                  <td>Total Liabilities</td>
                  <td class="num">{{ state.data.net_worth.opening ? fmt(state.data.net_worth.opening.total_liabilities_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.closing ? fmt(state.data.net_worth.closing.total_liabilities_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.opening && state.data.net_worth.closing
                      ? fmtSigned(state.data.net_worth.closing.total_liabilities_idr - state.data.net_worth.opening.total_liabilities_idr)
                      : '—' }}</td>
                </tr>
                <tr class="fs-row-total">
                  <td>Net Worth</td>
                  <td class="num">{{ state.data.net_worth.opening ? fmt(state.data.net_worth.opening.net_worth_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.closing ? fmt(state.data.net_worth.closing.net_worth_idr) : '—' }}</td>
                  <td class="num">{{ state.data.net_worth.delta_idr !== null ? fmtSigned(state.data.net_worth.delta_idr) : '—' }}</td>
                </tr>
              </tbody>
            </table>

            <div v-if="state.data.net_worth.closing" class="fs-grid-2">
              <div>
                <h4 class="fs-sub">Assets — Closing</h4>
                <table class="fs-table fs-table-compact">
                  <tbody>
                    <tr v-for="row in state.data.net_worth.closing.by_asset" :key="`a-${row.label}`">
                      <td>{{ row.label }}</td>
                      <td class="num">{{ fmt(row.idr) }}</td>
                    </tr>
                    <tr v-if="!state.data.net_worth.closing.by_asset.length">
                      <td colspan="2" class="muted">No assets recorded.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div>
                <h4 class="fs-sub">Liabilities — Closing</h4>
                <table class="fs-table fs-table-compact">
                  <tbody>
                    <tr v-for="row in state.data.net_worth.closing.by_liability" :key="`l-${row.label}`">
                      <td>{{ row.label }}</td>
                      <td class="num">{{ fmt(row.idr) }}</td>
                    </tr>
                    <tr v-if="!state.data.net_worth.closing.by_liability.length">
                      <td colspan="2" class="muted">No liabilities recorded.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          <!-- 2. Income & Expense -->
          <section class="fs-section">
            <h3 class="fs-section-title">2. Income &amp; Expense Summary</h3>
            <table class="fs-table fs-table-compact">
              <tbody>
                <tr>
                  <td>Total Income</td>
                  <td class="num">{{ fmt(state.data.income_expense.total_income_idr) }}</td>
                </tr>
                <tr>
                  <td>Total Expenses</td>
                  <td class="num">{{ fmt(state.data.income_expense.total_expense_idr) }}</td>
                </tr>
                <tr class="fs-row-total">
                  <td>Net Cash Flow</td>
                  <td class="num">{{ fmtSigned(state.data.income_expense.net_cash_flow_idr) }}</td>
                </tr>
              </tbody>
            </table>

            <h4 class="fs-sub">Expenses by Category</h4>
            <table class="fs-table fs-table-compact">
              <thead>
                <tr><th>Category</th><th class="num">Amount</th><th class="num">% of Expense</th><th class="num">#</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in state.data.income_expense.by_category.filter(r => r.amount < 0)" :key="`c-${row.category}`">
                  <td><span v-if="catIcon(row.category)" class="fs-cat-icon" v-html="catIcon(row.category)"></span>{{ row.category }}</td>
                  <td class="num">{{ fmt(row.amount) }}</td>
                  <td class="num">{{ row.pct_of_expense.toFixed(1) }}%</td>
                  <td class="num">{{ row.count }}</td>
                </tr>
                <tr v-if="!state.data.income_expense.by_category.some(r => r.amount < 0)">
                  <td colspan="4" class="muted">No expenses recorded.</td>
                </tr>
              </tbody>
            </table>

            <h4 class="fs-sub">By Owner</h4>
            <table class="fs-table fs-table-compact">
              <thead>
                <tr><th>Owner</th><th class="num">Income</th><th class="num">Expense</th><th class="num">Net</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in state.data.income_expense.by_owner" :key="`o-${row.owner}`">
                  <td>{{ row.owner || '(blank)' }}</td>
                  <td class="num">{{ fmt(row.income) }}</td>
                  <td class="num">{{ fmt(row.expense) }}</td>
                  <td class="num">{{ fmtSigned(row.net) }}</td>
                </tr>
              </tbody>
            </table>

            <h4 class="fs-sub">By Month</h4>
            <table class="fs-table fs-table-compact">
              <thead>
                <tr><th>Period</th><th class="num">Income</th><th class="num">Expense</th><th class="num">Net</th><th class="num">Needs Review</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in state.data.income_expense.by_month" :key="`m-${row.period}`">
                  <td>{{ row.period }}</td>
                  <td class="num">{{ fmt(row.total_income) }}</td>
                  <td class="num">{{ fmt(row.total_expense) }}</td>
                  <td class="num">{{ fmtSigned(row.net) }}</td>
                  <td class="num">{{ row.needs_review }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 3. Allocation -->
          <section class="fs-section">
            <h3 class="fs-section-title">3. Investment / Asset Allocation</h3>
            <h4 class="fs-sub">By Asset Class (Closing)</h4>
            <table class="fs-table fs-table-compact">
              <thead><tr><th>Class</th><th class="num">Value</th><th class="num">% of Assets</th></tr></thead>
              <tbody>
                <tr v-for="row in state.data.allocation.by_asset_class" :key="`ac-${row.label}`">
                  <td>{{ row.label }}</td>
                  <td class="num">{{ fmt(row.idr) }}</td>
                  <td class="num">{{ row.pct.toFixed(1) }}%</td>
                </tr>
                <tr v-if="!state.data.allocation.by_asset_class.length">
                  <td colspan="3" class="muted">No closing-snapshot data.</td>
                </tr>
              </tbody>
            </table>

            <h4 class="fs-sub">By Institution (Closing)</h4>
            <table class="fs-table fs-table-compact">
              <thead><tr><th>Institution</th><th class="num">Assets</th><th class="num">Liabilities</th><th class="num">Net</th></tr></thead>
              <tbody>
                <tr v-for="row in state.data.allocation.by_institution" :key="`bi-${row.institution}`">
                  <td>{{ row.institution }}</td>
                  <td class="num">{{ fmt(row.assets_idr) }}</td>
                  <td class="num">{{ fmt(row.liabilities_idr) }}</td>
                  <td class="num">{{ fmtSigned(row.net_idr) }}</td>
                </tr>
                <tr v-if="!state.data.allocation.by_institution.length">
                  <td colspan="4" class="muted">No closing-snapshot data.</td>
                </tr>
              </tbody>
            </table>

            <h4 class="fs-sub">By Account # (Closing)</h4>
            <table class="fs-table fs-table-compact">
              <thead><tr><th>Institution</th><th>Account #</th><th class="num">Assets</th><th class="num">Liabilities</th><th class="num">Net</th></tr></thead>
              <tbody>
                <tr v-for="row in state.data.allocation.by_account" :key="`ba-${row.institution}-${row.account}`">
                  <td>{{ row.institution }}</td>
                  <td class="mono">{{ row.account }}</td>
                  <td class="num">{{ fmt(row.assets_idr) }}</td>
                  <td class="num">{{ fmt(row.liabilities_idr) }}</td>
                  <td class="num">{{ fmtSigned(row.net_idr) }}</td>
                </tr>
                <tr v-if="!state.data.allocation.by_account.length">
                  <td colspan="5" class="muted">No account-level data.</td>
                </tr>
              </tbody>
            </table>
          </section>

          <!-- 4. Cash Flow -->
          <section class="fs-section">
            <h3 class="fs-section-title">4. Cash Flow Summary</h3>
            <table class="fs-table fs-table-compact">
              <tbody>
                <tr>
                  <td>Opening Net Worth <small class="muted">{{ state.data.cash_flow.opening_date || '—' }}</small></td>
                  <td class="num">{{ state.data.cash_flow.opening_balance_idr !== null ? fmt(state.data.cash_flow.opening_balance_idr) : '—' }}</td>
                </tr>
                <tr>
                  <td>(+) Inflows (income)</td>
                  <td class="num">{{ fmt(state.data.cash_flow.inflows_idr) }}</td>
                </tr>
                <tr>
                  <td>(−) Outflows (expenses)</td>
                  <td class="num">{{ fmt(state.data.cash_flow.outflows_idr) }}</td>
                </tr>
                <tr>
                  <td>Closing Net Worth <small class="muted">{{ state.data.cash_flow.closing_date || '—' }}</small></td>
                  <td class="num">{{ state.data.cash_flow.closing_balance_idr !== null ? fmt(state.data.cash_flow.closing_balance_idr) : '—' }}</td>
                </tr>
                <tr class="fs-row-total">
                  <td>Unexplained Δ <small class="muted">(market drift, FX, unrecorded txns)</small></td>
                  <td class="num">{{ state.data.cash_flow.unexplained_delta_idr !== null ? fmtSigned(state.data.cash_flow.unexplained_delta_idr) : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fs-modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.45);
  z-index: 200;
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.fs-modal-sheet {
  background: var(--card);
  border-radius: var(--radius-lg);
  width: 100%; max-width: 880px;
  max-height: 92dvh;
  display: flex; flex-direction: column;
  overflow: hidden;
}
.fs-modal-header {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}
.fs-modal-header .modal-title { flex: 1; font-size: 16px; font-weight: 700; }
.fs-modal-header .modal-close {
  width: 32px; height: 32px;
  border: none; background: var(--bg); border-radius: 50%;
  font-size: 16px; cursor: pointer; color: var(--neutral);
  display: flex; align-items: center; justify-content: center;
}
.fs-modal-body {
  padding: 16px;
  overflow-y: auto;
}
.fs-report-head { margin-bottom: 12px; }
.fs-report-title { font-size: 18px; font-weight: 700; }
.fs-report-sub   { font-size: 14px; color: var(--neutral); margin-top: 2px; }
.fs-report-meta  { font-size: 12px; color: var(--neutral); margin-top: 2px; }

.fs-section { margin-top: 18px; }
.fs-section-title {
  font-size: 15px; font-weight: 700; margin: 0 0 8px;
  border-bottom: 1px solid var(--border); padding-bottom: 4px;
}
.fs-sub { font-size: 13px; font-weight: 600; margin: 12px 0 6px; color: var(--neutral); }

.fs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.fs-table th, .fs-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}
.fs-table th { font-weight: 600; color: var(--neutral); }
.fs-table td.num, .fs-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
.fs-table .muted { color: var(--neutral); font-style: italic; }
.fs-row-total td { font-weight: 700; border-top: 2px solid var(--border); }
.fs-cat-icon { width: 14px; height: 14px; display: inline-flex; align-items: center; justify-content: center; color: var(--primary-deep, currentColor); margin-right: 4px; vertical-align: -2px; }
.fs-cat-icon :deep(svg) { width: 14px; height: 14px; }
.mono { font-family: var(--font-mono, monospace); font-size: 12px; letter-spacing: 0.02em; }

.fs-grid-2 {
  display: grid; gap: 16px;
  grid-template-columns: 1fr 1fr;
  margin-top: 12px;
}
@media (max-width: 640px) {
  .fs-grid-2 { grid-template-columns: 1fr; }
}

.alert.alert-warn {
  background: #fff8e1;
  border: 1px solid #f0c869;
  color: #6b4d00;
  border-radius: var(--radius);
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}

.btn-ghost {
  background: transparent;
  border: 1px solid var(--border);
}

@media print {
  .no-print { display: none !important; }
  .fs-modal-overlay {
    position: static !important;
    background: transparent !important;
    padding: 0 !important;
  }
  .fs-modal-sheet {
    box-shadow: none !important;
    max-height: none !important;
    max-width: none !important;
    border-radius: 0 !important;
  }
  .fs-modal-body { overflow: visible !important; }
}
</style>
