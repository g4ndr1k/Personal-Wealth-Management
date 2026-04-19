<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useFinanceStore } from '../stores/finance.js'
import { api } from '../api/client.js'
import { useFmt } from '../composables/useFmt.js'

const store = useFinanceStore()

// ── State ─────────────────────────────────────────────────────────────────────
const snapshotDate    = ref('')
const snapshotDates   = ref([])
const showMonthPicker = ref(false)
const newMonthInput   = ref('')

const loading   = ref(false)
const loadError = ref(null)
const toast     = ref('')

const realEstate = ref([])   // holdings with asset_class === 'real_estate'
const jamsostek  = ref([])   // holdings with asset_class === 'retirement'

// Per-row edit state: { [holding.id]: { value, date, saving, error } }
const edits = ref({})

// ── Helpers ───────────────────────────────────────────────────────────────────
const { fmt } = useFmt()

function fmtDateChip(d) {
  if (!d) return ''
  const [y, m] = d.split('-')
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${MONTHS[parseInt(m, 10) - 1]} ${y}`
}

function monthKey(d) { return (d || '').slice(0, 7) }

function collapseMonthDates(dateList) {
  const seen = new Set()
  const out = []
  for (const d of dateList || []) {
    const key = monthKey(d)
    if (!key || seen.has(key)) continue
    out.push(d)
    seen.add(key)
  }
  return out
}

function lastDayOfMonth(yyyyMM) {
  if (!yyyyMM) return ''
  const [y, m] = yyyyMM.split('-').map(Number)
  const lastDay = new Date(y, m, 0).getDate()
  return `${y}-${String(m).padStart(2,'0')}-${String(lastDay).padStart(2,'0')}`
}

function today() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
}

// ── Month navigation ──────────────────────────────────────────────────────────
const currentDateIndex = computed(() => snapshotDates.value.indexOf(snapshotDate.value))
const isNewestDate = computed(() => currentDateIndex.value <= 0)
const isOldestDate = computed(() => currentDateIndex.value >= snapshotDates.value.length - 1 || !snapshotDates.value.length)

function prevMonth() {
  const idx = currentDateIndex.value
  if (idx < snapshotDates.value.length - 1) selectDate(snapshotDates.value[idx + 1])
}
function nextMonth() {
  const idx = currentDateIndex.value
  if (idx > 0) selectDate(snapshotDates.value[idx - 1])
}
function selectDate(d) {
  snapshotDate.value = d
}
function pickMonth(val) {
  if (!val) { showMonthPicker.value = false; return }
  const existing = snapshotDates.value.find(d => monthKey(d) === val)
  if (existing) {
    showMonthPicker.value = false; newMonthInput.value = ''
    snapshotDate.value = existing; return
  }
  const dateStr = lastDayOfMonth(val)
  if (!dateStr) return
  showMonthPicker.value = false; newMonthInput.value = ''
  if (!snapshotDates.value.includes(dateStr))
    snapshotDates.value = [dateStr, ...snapshotDates.value].sort().reverse()
  snapshotDate.value = dateStr
}

// ── Load holdings ─────────────────────────────────────────────────────────────
async function loadItems({ fresh = false } = {}) {
  if (!snapshotDate.value) return
  loading.value = true
  loadError.value = null
  try {
    // fresh=true bypasses the IDB GET cache so post-save reloads see the
    // just-written value rather than the 24h-cached pre-save value.
    const holds = await api.getHoldings(
      { snapshot_date: snapshotDate.value },
      fresh ? { forceFresh: true } : {},
    )
    realEstate.value = holds.filter(h => h.asset_class === 'real_estate')
    jamsostek.value  = holds.filter(h => h.asset_class === 'retirement')
    // Initialise edit state for each row
    const next = {}
    for (const h of [...realEstate.value, ...jamsostek.value]) {
      next[h.id] = {
        value: h.market_value_idr    ?? 0,
        date:  h.last_appraised_date || today(),
        pnl:   h.unrealised_pnl_idr  ?? 0,
        saving: false,
        error:  '',
      }
    }
    edits.value = next
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

// Wrap so Vue doesn't pass the new date string as the first argument.
watch(snapshotDate, () => loadItems())

// ── Save a single row ─────────────────────────────────────────────────────────
async function saveRow(h) {
  const e = edits.value[h.id]
  if (!e) return
  e.saving = true
  e.error  = ''
  try {
    await api.upsertHolding({
      snapshot_date:       snapshotDate.value,
      asset_class:         h.asset_class,
      asset_name:          h.asset_name,
      isin_or_code:        h.isin_or_code        || '',
      institution:         h.institution         || '',
      account:             h.account             || '',
      owner:               h.owner               || '',
      currency:            h.currency            || 'IDR',
      quantity:            h.quantity            ?? 0,
      unit_price:          h.unit_price          ?? 0,
      market_value:        e.value,
      market_value_idr:    e.value,
      cost_basis:          h.cost_basis          ?? 0,
      cost_basis_idr:      h.cost_basis_idr      ?? 0,
      unrealised_pnl_idr:  e.pnl,
      exchange_rate:       h.exchange_rate        ?? 0,
      maturity_date:       h.maturity_date        || '',
      coupon_rate:         h.coupon_rate          ?? 0,
      last_appraised_date: e.date,
      notes:               h.notes               || '',
    })
    await api.createSnapshot({ snapshot_date: snapshotDate.value })
    showToast('Saved ✓')
    await loadItems({ fresh: true })
  } catch (err) {
    e.error = err.message
  } finally {
    e.saving = false
  }
}

function showToast(msg) {
  toast.value = msg
  setTimeout(() => { toast.value = '' }, 2500)
}

// ── Init ──────────────────────────────────────────────────────────────────────
onMounted(async () => {
  try {
    snapshotDates.value = collapseMonthDates(await api.wealthSnapshotDates())
    if (snapshotDates.value.length) {
      const endMonth = store.dashboardEndMonth || ''
      if (endMonth) {
        const clamped = snapshotDates.value.find(d => monthKey(d) <= endMonth)
        snapshotDate.value = clamped || snapshotDates.value[snapshotDates.value.length - 1]
      } else {
        snapshotDate.value = snapshotDates.value[0]
      }
    }
  } catch (_) {
    const now = new Date()
    snapshotDate.value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
  }
})
</script>

<template>
  <div>
    <!-- Month navigation -->
    <div class="month-nav" style="padding:0 16px">
      <button class="nav-btn" @click="prevMonth" :disabled="isOldestDate">‹</button>
      <div class="month-nav-center">
        <template v-if="!showMonthPicker">
          <span class="month-label">{{ fmtDateChip(snapshotDate) || '—' }}</span>
          <button class="nav-btn nav-btn-sm" @click="showMonthPicker = true" title="Jump to month">+</button>
        </template>
        <template v-else>
          <input
            type="month"
            class="month-picker-inline"
            :value="newMonthInput"
            @change="pickMonth($event.target.value)"
            @blur="showMonthPicker = false"
            autofocus
          />
        </template>
      </div>
      <button class="nav-btn" @click="nextMonth" :disabled="isNewestDate">›</button>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading"><div class="spinner"></div></div>

    <!-- Error -->
    <div v-else-if="store.isReadOnly" class="alert" style="margin:12px 16px;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8">
      👁 Read-only — adjustments are not available in NAS replica mode.
    </div>

    <div v-else-if="loadError" class="alert alert-error" style="margin:12px 16px">
      ❌ {{ loadError }}
      <button class="btn btn-sm btn-ghost" @click="loadItems" style="margin-left:auto">Retry</button>
    </div>

    <template v-else>
      <!-- Real Estate section -->
      <div class="section-header">
        <span>🏠 Real Estate</span>
        <span class="section-header-total">{{ fmt(realEstate.reduce((s,h) => s + (h.market_value_idr||0), 0)) }}</span>
      </div>

      <div v-if="!realEstate.length" class="empty-state-inline">No real estate entries for this month</div>

      <div v-for="h in realEstate" :key="h.id" class="adj-card">
        <div class="adj-card-header">
          <span class="adj-name">{{ h.asset_name }}</span>
          <span class="adj-current">{{ fmt(h.market_value_idr) }}</span>
        </div>
        <div v-if="h.last_appraised_date" class="adj-meta">
          appraised {{ h.last_appraised_date }}
        </div>
        <template v-if="edits[h.id]">
          <div class="adj-fields">
            <div class="adj-field">
              <label class="adj-label">New Value (IDR)</label>
              <input
                type="number"
                class="adj-input"
                v-model.number="edits[h.id].value"
                placeholder="0"
                step="1000000"
              />
            </div>
            <div class="adj-field">
              <label class="adj-label">Appraisal Date</label>
              <input
                type="date"
                class="adj-input"
                v-model="edits[h.id].date"
              />
            </div>
          </div>
          <div class="adj-fields" style="margin-top:0">
            <div class="adj-field">
              <label class="adj-label">Unrealized P&amp;L (IDR)</label>
              <input
                type="number"
                class="adj-input"
                v-model.number="edits[h.id].pnl"
                placeholder="0"
                step="1000000"
              />
            </div>
          </div>
          <div v-if="edits[h.id].error" class="adj-error">{{ edits[h.id].error }}</div>
          <button
            class="btn btn-primary adj-save-btn"
            @click="saveRow(h)"
            :disabled="edits[h.id].saving"
          >
            {{ edits[h.id].saving ? 'Saving…' : 'Save' }}
          </button>
        </template>
      </div>

      <!-- Jamsostek / Retirement section -->
      <div class="section-header" style="margin-top:8px">
        <span>🏦 Jamsostek / Retirement</span>
        <span class="section-header-total">{{ fmt(jamsostek.reduce((s,h) => s + (h.market_value_idr||0), 0)) }}</span>
      </div>

      <div v-if="!jamsostek.length" class="empty-state-inline">No retirement entries for this month</div>

      <div v-for="h in jamsostek" :key="h.id" class="adj-card">
        <div class="adj-card-header">
          <span class="adj-name">{{ h.asset_name }}</span>
          <span class="adj-current">{{ fmt(h.market_value_idr) }}</span>
        </div>
        <div v-if="h.last_appraised_date" class="adj-meta">
          updated {{ h.last_appraised_date }}
        </div>
        <template v-if="edits[h.id]">
          <div class="adj-fields">
            <div class="adj-field">
              <label class="adj-label">New Balance (IDR)</label>
              <input
                type="number"
                class="adj-input"
                v-model.number="edits[h.id].value"
                placeholder="0"
                step="100000"
              />
            </div>
            <div class="adj-field">
              <label class="adj-label">Statement Date</label>
              <input
                type="date"
                class="adj-input"
                v-model="edits[h.id].date"
              />
            </div>
          </div>
          <div class="adj-fields" style="margin-top:0">
            <div class="adj-field">
              <label class="adj-label">Unrealized P&amp;L (IDR)</label>
              <input
                type="number"
                class="adj-input"
                v-model.number="edits[h.id].pnl"
                placeholder="0"
                step="100000"
              />
            </div>
          </div>
          <div v-if="edits[h.id].error" class="adj-error">{{ edits[h.id].error }}</div>
          <button
            class="btn btn-primary adj-save-btn"
            @click="saveRow(h)"
            :disabled="edits[h.id].saving"
          >
            {{ edits[h.id].saving ? 'Saving…' : 'Save' }}
          </button>
        </template>
      </div>
    </template>

    <!-- Toast -->
    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
/* ── Month nav centre ─────────────────────────────────────────────────────── */
.month-nav-center {
  display: flex;
  align-items: center;
  gap: 8px;
}
.nav-btn-sm {
  width: 26px; height: 26px; font-size: 16px; padding: 0; line-height: 1;
  border-radius: 50%; border: 1.5px dashed var(--primary);
  background: transparent; color: var(--primary); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s;
}
.nav-btn-sm:hover { background: var(--primary-dim); }
.month-picker-inline {
  border: 1.5px solid var(--primary); border-radius: 20px;
  padding: 4px 10px; font-size: 13px; background: var(--card);
  color: var(--text); outline: none; height: 32px;
}

/* ── Section headers ─────────────────────────────────────────────────────── */
.section-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 16px 6px;
  font-size: 13px; font-weight: 700; color: var(--primary);
  text-transform: uppercase; letter-spacing: 0.06em;
}
.section-header-total {
  font-size: 13px; font-weight: 700; color: var(--text); letter-spacing: 0;
  text-transform: none;
}

/* ── Adjustment cards ────────────────────────────────────────────────────── */
.adj-card {
  margin: 0 16px 12px;
  padding: 14px 16px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
}
.adj-card-header {
  display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 2px;
}
.adj-name {
  font-size: 15px; font-weight: 600; color: var(--text);
}
.adj-current {
  font-size: 15px; font-weight: 700; color: var(--text-muted);
}
.adj-meta {
  font-size: 12px; color: var(--text-muted); margin-bottom: 12px;
}
.adj-fields {
  display: flex; gap: 10px; margin-bottom: 10px;
}
.adj-field {
  flex: 1; min-width: 0;
}
.adj-label {
  display: block; font-size: 11px; font-weight: 600;
  color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.05em; margin-bottom: 4px;
}
.adj-input {
  width: 100%; padding: 8px 10px;
  border: 1.5px solid var(--border); border-radius: 10px;
  background: var(--bg); color: var(--text);
  font-size: 14px; outline: none;
  box-sizing: border-box;
}
.adj-input:focus { border-color: var(--primary); }
.adj-save-btn {
  width: 100%; margin-top: 2px;
}
.adj-error {
  font-size: 12px; color: var(--expense); margin-bottom: 6px;
}

/* ── Empty / toast ───────────────────────────────────────────────────────── */
.empty-state-inline {
  padding: 12px 16px; font-size: 13px; color: var(--text-muted);
}
</style>
