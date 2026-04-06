<template>
  <div>
    <!-- Group filter tabs -->
    <div class="group-tabs-wrap">
      <div class="group-tabs">
        <button
          v-for="t in TABS"
          :key="t.key"
          :class="['group-tab', activeTab === t.key && 'active']"
          @click="activeTab = t.key"
        >{{ t.icon }} {{ t.label }}</button>
      </div>
    </div>

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
    <div v-else-if="loadError" class="alert alert-error" style="margin:12px 16px">
      ❌ {{ loadError }}
      <button class="btn btn-sm btn-ghost" @click="loadItems" style="margin-left:auto">Retry</button>
    </div>

    <!-- Item list -->
    <template v-else>
      <!-- Cash & Liquid -->
      <template v-if="activeTab === 'all' || activeTab === 'cash'">
        <div class="section-header">
          <span>🏦 Cash &amp; Liquid</span>
          <span class="section-total">{{ fmt(totals.liquid) }}</span>
        </div>
        <div v-if="!filteredBalances.length" class="empty-state-inline">No entries yet</div>
        <div
          v-for="b in filteredBalances"
          :key="`bal-${b.id}`"
          class="asset-item"
        >
          <div class="asset-main">
            <span class="asset-name">{{ b.institution }}</span>
            <span class="asset-sub">{{ b.account }} · {{ formatAccountType(b.account_type) }}</span>
          </div>
          <div class="asset-right">
            <span class="asset-value">{{ fmt(b.balance_idr) }}</span>
            <span v-if="b.currency && b.currency !== 'IDR'" class="asset-fx">
              {{ b.currency }} {{ fmtForeign(b.balance, b.currency) }}
              <template v-if="b.exchange_rate > 0"> · {{ fmtRate(b.exchange_rate) }}/{{ b.currency }}</template>
            </span>
          </div>
          <button class="asset-del" @click.stop="deleteItem('balance', b.id)" title="Delete">✕</button>
        </div>
      </template>

      <!-- Investments -->
      <template v-if="activeTab === 'all' || activeTab === 'investments'">
        <div class="section-header">
          <span>📈 Investments</span>
          <span class="section-total">{{ fmt(totals.investments) }}</span>
        </div>
        <div v-if="!filteredInvestments.length" class="empty-state-inline">No entries yet</div>

        <!-- Government Bonds sub-group -->
        <template v-if="filteredBonds.length">
          <div class="sub-header">
            <span>🏛 Government Bonds</span>
            <span class="sub-total">{{ fmt(filteredBonds.reduce((s,h) => s + (h.market_value_idr||0), 0)) }}</span>
          </div>
          <div
            v-for="h in filteredBonds"
            :key="`hld-${h.id}`"
            class="asset-item"
          >
            <div class="asset-main">
              <div class="asset-name-row">
                <span class="asset-name">{{ h.asset_name }}</span>
                <span v-if="h.unit_price > 0"
                  :class="['price-badge', h.unit_price >= 100 ? 'premium' : 'discount']"
                  :title="h.unit_price >= 100 ? 'Trading at premium' : 'Trading at discount'"
                >{{ h.unit_price.toFixed(3) }}</span>
              </div>
              <span class="asset-sub">
                Gov't Bond · {{ h.currency }}
                <template v-if="h.institution"> · {{ h.institution }}</template>
                <template v-if="h.quantity"> · Face {{ fmtForeign(h.quantity, h.currency) }}</template>
              </span>
            </div>
            <div class="asset-right">
              <span class="asset-value">{{ fmt(h.market_value_idr) }}</span>
              <span v-if="h.currency !== 'IDR' && h.exchange_rate > 0" class="asset-fx">
                {{ h.currency }} {{ fmtForeign(h.market_value, h.currency) }}
                · {{ fmtRate(h.exchange_rate) }}/{{ h.currency }}
              </span>
              <span v-if="h.unrealised_pnl_idr !== 0"
                :class="h.unrealised_pnl_idr >= 0 ? 'text-income' : 'text-expense'"
                style="font-size:12px">
                {{ h.unrealised_pnl_idr >= 0 ? '+' : '' }}{{ fmt(h.unrealised_pnl_idr) }}
              </span>
            </div>
            <button class="asset-del" @click.stop="deleteItem('holding', h.id)" title="Delete">✕</button>
          </div>
        </template>

        <!-- Other investments sub-group -->
        <template v-if="filteredOtherInvestments.length">
          <div v-if="filteredBonds.length" class="sub-header">
            <span>📊 Other Investments</span>
            <span class="sub-total">{{ fmt(filteredOtherInvestments.reduce((s,h) => s + (h.market_value_idr||0), 0)) }}</span>
          </div>
          <div
            v-for="h in filteredOtherInvestments"
            :key="`hld-${h.id}`"
            class="asset-item"
          >
            <div class="asset-main">
              <span class="asset-name">{{ h.asset_name }}</span>
              <span class="asset-sub">
                {{ formatAssetClass(h.asset_class) }}
                <template v-if="h.institution"> · {{ h.institution }}</template>
                <template v-if="h.maturity_date"> · matures {{ h.maturity_date }}</template>
              </span>
            </div>
            <div class="asset-right">
              <span class="asset-value">{{ fmt(h.market_value_idr) }}</span>
              <span v-if="h.unrealised_pnl_idr !== 0" :class="h.unrealised_pnl_idr >= 0 ? 'text-income' : 'text-expense'" style="font-size:12px">
                {{ h.unrealised_pnl_idr >= 0 ? '+' : '' }}{{ fmt(h.unrealised_pnl_idr) }}
              </span>
            </div>
            <button class="asset-del" @click.stop="deleteItem('holding', h.id)" title="Delete">✕</button>
          </div>
        </template>
      </template>

      <!-- Real Estate -->
      <template v-if="activeTab === 'all' || activeTab === 'realestate'">
        <div class="section-header">
          <span>🏠 Real Estate</span>
          <span class="section-total">{{ fmt(totals.realestate) }}</span>
        </div>
        <div v-if="!filteredRealEstate.length" class="empty-state-inline">No entries yet</div>
        <div
          v-for="h in filteredRealEstate"
          :key="`re-${h.id}`"
          class="asset-item asset-item-tappable"
          @click="editItem('holding', h)"
        >
          <div class="asset-main">
            <span class="asset-name">{{ h.asset_name }}</span>
            <span class="asset-sub">
              Real Estate
              <template v-if="h.notes"> · {{ h.notes }}</template>
              <template v-if="h.last_appraised_date"> · appraised {{ h.last_appraised_date }}</template>
            </span>
          </div>
          <div class="asset-right">
            <span class="asset-value">{{ fmt(h.market_value_idr) }}</span>
          </div>
          <button class="asset-del" @click.stop="deleteItem('holding', h.id)" title="Delete">✕</button>
        </div>
      </template>

      <!-- Physical Assets -->
      <template v-if="activeTab === 'all' || activeTab === 'physical'">
        <div class="section-header">
          <span>🚗 Physical Assets</span>
          <span class="section-total">{{ fmt(totals.physical) }}</span>
        </div>
        <div v-if="!filteredPhysical.length" class="empty-state-inline">No entries yet</div>
        <div
          v-for="h in filteredPhysical"
          :key="`ph-${h.id}`"
          class="asset-item"
        >
          <div class="asset-main">
            <span class="asset-name">{{ h.asset_name }}</span>
            <span class="asset-sub">{{ formatAssetClass(h.asset_class) }}</span>
          </div>
          <div class="asset-right">
            <span class="asset-value">{{ fmt(h.market_value_idr) }}</span>
          </div>
          <button class="asset-del" @click.stop="deleteItem('holding', h.id)" title="Delete">✕</button>
        </div>
      </template>

      <!-- Liabilities -->
      <template v-if="activeTab === 'all' || activeTab === 'liabilities'">
        <div class="section-header section-header-liab">
          <span>🔴 Liabilities</span>
          <span class="section-total text-expense">{{ fmt(totals.liabilities) }}</span>
        </div>
        <div v-if="!filteredLiabilities.length" class="empty-state-inline">No entries yet</div>
        <div
          v-for="l in filteredLiabilities"
          :key="`liab-${l.id}`"
          class="asset-item"
        >
          <div class="asset-main">
            <span class="asset-name">{{ l.liability_name }}</span>
            <span class="asset-sub">
              {{ formatLiabType(l.liability_type) }}
              <template v-if="l.institution"> · {{ l.institution }}</template>
              <template v-if="l.due_date"> · due {{ l.due_date }}</template>
            </span>
          </div>
          <div class="asset-right">
            <span class="asset-value text-expense">{{ fmt(l.balance_idr) }}</span>
          </div>
          <button class="asset-del" @click.stop="deleteItem('liability', l.id)" title="Delete">✕</button>
        </div>
      </template>

      <!-- Generate snapshot -->
      <div style="padding:16px">
        <button
          class="btn btn-primary"
          style="width:100%"
          @click="generateSnapshot"
          :disabled="generating || !snapshotDate"
        >
          {{ generating ? 'Saving snapshot…' : `Save Snapshot for ${fmtDateChip(snapshotDate)}` }}
        </button>
        <p v-if="snapMsg" class="snap-msg" :class="snapMsgOk ? 'ok' : 'err'">{{ snapMsg }}</p>
      </div>
    </template>

    <!-- Toast -->
    <div v-if="toast" class="toast">{{ toast }}</div>

    <!-- FAB: open add form -->
    <button class="holdings-fab" @click="openForm(null)" title="Add entry">+</button>

    <!-- Modal form -->
    <div v-if="showForm" class="modal-overlay" @click.self="showForm = false">
      <div class="modal-sheet">
        <div class="modal-header">
          <span class="modal-title">
            {{ editingId ? 'Edit' : 'Add' }}
            {{ formMode === 'balance' ? 'Balance' : formMode === 'liability' ? 'Liability' : 'Holding' }}
          </span>
          <button class="modal-close" @click="showForm = false">✕</button>
        </div>

        <!-- Form type selector (hidden when editing an existing item) -->
        <div v-if="!editingId" class="form-type-tabs">
          <button :class="['form-type-tab', formMode === 'balance'   && 'active']" @click="formMode = 'balance'">💰 Balance</button>
          <button :class="['form-type-tab', formMode === 'holding'   && 'active']" @click="formMode = 'holding'">📈 Holding</button>
          <button :class="['form-type-tab', formMode === 'liability' && 'active']" @click="formMode = 'liability'">🔴 Liability</button>
        </div>

        <!-- Balance form -->
        <div v-if="formMode === 'balance'" class="form-body">
          <label>Institution *</label>
          <input v-model="form.institution" placeholder="e.g. Permata, Maybank, BCA" />
          <label>Account / Label *</label>
          <input v-model="form.account" placeholder="e.g. 1234-5678 or Tabungan" />
          <label>Account Type</label>
          <select v-model="form.account_type">
            <option value="savings">Savings Account</option>
            <option value="checking">Checking Account</option>
            <option value="money_market">Money Market</option>
            <option value="physical_cash">Physical Cash</option>
          </select>
          <label>Currency</label>
          <select v-model="form.currency">
            <option value="IDR">IDR (Indonesian Rupiah)</option>
            <option value="USD">USD (US Dollar)</option>
            <option value="EUR">EUR (Euro)</option>
            <option value="SGD">SGD (Singapore Dollar)</option>
            <option value="JPY">JPY (Japanese Yen)</option>
            <option value="AUD">AUD (Australian Dollar)</option>
          </select>
          <label>Owner</label>
          <select v-model="form.owner">
            <option value="">— All —</option>
            <option v-for="o in owners" :key="o" :value="o">{{ o }}</option>
          </select>
          <!-- For non-IDR accounts: enter foreign amount + rate; IDR is auto-calculated -->
          <template v-if="form.currency !== 'IDR'">
            <label>Balance ({{ form.currency }}) *</label>
            <input type="number" v-model.number="form.balance" placeholder="0.00" min="0" step="0.01" />
            <label>Exchange Rate (1 {{ form.currency }} = ? IDR)</label>
            <input type="number" v-model.number="form.exchange_rate" placeholder="e.g. 16300" min="0" step="0.01"
              @input="form.balance_idr = Math.round(form.balance * form.exchange_rate)" />
            <label>Balance IDR (auto-calculated)</label>
            <input type="number" v-model.number="form.balance_idr" placeholder="0" min="0" readonly style="background:var(--bg-subtle,#f5f5f5);opacity:.8" />
          </template>
          <template v-else>
            <label>Balance (IDR) *</label>
            <input type="number" v-model.number="form.balance_idr" placeholder="0" min="0" />
          </template>
          <label>Notes</label>
          <input v-model="form.notes" placeholder="Optional" />
        </div>

        <!-- Holding form -->
        <div v-if="formMode === 'holding'" class="form-body">
          <label>Asset Class *</label>
          <select v-model="form.asset_class">
            <optgroup label="Investments">
              <option value="bond">Bond / Fixed Income</option>
              <option value="stock">Stock</option>
              <option value="mutual_fund">Mutual Fund</option>
              <option value="retirement">Retirement (Jamsostek)</option>
              <option value="crypto">Crypto</option>
            </optgroup>
            <optgroup label="Real Estate">
              <option value="real_estate">Real Estate</option>
            </optgroup>
            <optgroup label="Physical Assets">
              <option value="vehicle">Vehicle</option>
              <option value="gold">Gold &amp; Precious Metals</option>
              <option value="other">Other</option>
            </optgroup>
          </select>
          <label>Name *</label>
          <input v-model="form.asset_name" placeholder="e.g. ORI029T6, BMRI, Rumah Menteng" />
          <label>Ticker / ISIN</label>
          <input v-model="form.isin_or_code" placeholder="Optional" />
          <label>Institution</label>
          <input v-model="form.institution" placeholder="e.g. Permata Sekuritas" />
          <label>Owner</label>
          <select v-model="form.owner">
            <option value="">— All —</option>
            <option v-for="o in owners" :key="o" :value="o">{{ o }}</option>
          </select>
          <label>Market Value (IDR) *</label>
          <input type="number" v-model.number="form.market_value_idr" placeholder="0" min="0" />
          <label>Quantity</label>
          <input type="number" v-model.number="form.quantity" placeholder="0" />
          <label>Unit Price</label>
          <input type="number" v-model.number="form.unit_price" placeholder="0" />
          <label>Cost Basis (IDR)</label>
          <input type="number" v-model.number="form.cost_basis_idr" placeholder="0" />
          <template v-if="form.asset_class === 'bond'">
            <label>Maturity Date</label>
            <input type="date" v-model="form.maturity_date" />
            <label>Coupon Rate (%)</label>
            <input type="number" v-model.number="form.coupon_rate" placeholder="e.g. 6.5" step="0.01" />
          </template>
          <template v-if="form.asset_class === 'real_estate' || form.asset_class === 'vehicle' || form.asset_class === 'gold' || form.asset_class === 'other'">
            <label>Last Appraised Date</label>
            <input type="date" v-model="form.last_appraised_date" />
          </template>
          <label>Notes</label>
          <input v-model="form.notes" placeholder="Optional" />
        </div>

        <!-- Liability form -->
        <div v-if="formMode === 'liability'" class="form-body">
          <label>Type *</label>
          <select v-model="form.liability_type">
            <option value="mortgage">Mortgage (KPR)</option>
            <option value="personal_loan">Personal Loan</option>
            <option value="credit_card">Credit Card Outstanding</option>
            <option value="taxes_owed">Taxes Owed</option>
            <option value="other">Other</option>
          </select>
          <label>Name / Label *</label>
          <input v-model="form.liability_name" placeholder="e.g. BCA Credit Card, KPR Mandiri" />
          <label>Institution</label>
          <input v-model="form.institution" placeholder="e.g. BCA, Mandiri" />
          <label>Owner</label>
          <select v-model="form.owner">
            <option value="">— All —</option>
            <option v-for="o in owners" :key="o" :value="o">{{ o }}</option>
          </select>
          <label>Outstanding Balance (IDR) *</label>
          <input type="number" v-model.number="form.balance_idr" placeholder="0" min="0" />
          <label>Due Date</label>
          <input type="date" v-model="form.due_date" />
          <label>Notes</label>
          <input v-model="form.notes" placeholder="Optional" />
        </div>

        <!-- Save button -->
        <div style="padding:16px">
          <button class="btn btn-primary" style="width:100%" @click="saveForm" :disabled="saving">
            {{ saving ? 'Saving…' : 'Save' }}
          </button>
          <p v-if="formError" class="snap-msg err">{{ formError }}</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const route = useRoute()
const store = useFinanceStore()

// ── Tabs ──────────────────────────────────────────────────────────────────────
const TABS = [
  { key: 'all',         label: 'All',          icon: '🗂️' },
  { key: 'cash',        label: 'Cash',         icon: '🏦' },
  { key: 'investments', label: 'Investments',  icon: '📈' },
  { key: 'realestate',  label: 'Real Estate',  icon: '🏠' },
  { key: 'physical',    label: 'Physical',     icon: '🚗' },
  { key: 'liabilities', label: 'Liabilities',  icon: '🔴' },
]

const GROUP_TO_TAB = {
  'Cash & Liquid':   'cash',
  'Investments':     'investments',
  'Real Estate':     'realestate',
  'Physical Assets': 'physical',
  'Liabilities':     'liabilities',
}

const activeTab = ref(GROUP_TO_TAB[route.query.group] || 'all')

// ── State ─────────────────────────────────────────────────────────────────────
const snapshotDate    = ref('')            // YYYY-MM-DD, e.g. 2026-03-31
const snapshotDates   = ref([])           // available month-end dates from API
const showMonthPicker = ref(false)        // toggle inline <input type="month">
const newMonthInput   = ref('')

const loading      = ref(false)
const loadError    = ref(null)
const generating   = ref(false)
const snapMsg      = ref('')
const snapMsgOk    = ref(true)
const toast        = ref('')
const showForm     = ref(false)
const saving       = ref(false)
const formError    = ref('')
const formMode     = ref('balance')   // 'balance' | 'holding' | 'liability'
const editingId    = ref(null)        // null = add mode, number = edit mode (row id)

const balances    = ref([])
const holdings    = ref([])
const liabilities = ref([])

const owners = computed(() => store.owners)

// ── Default form state ────────────────────────────────────────────────────────
const FORM_DEFAULTS = {
  institution: '', account: '', account_type: 'savings',
  asset_class: 'bond', asset_name: '', isin_or_code: '',
  liability_type: 'credit_card', liability_name: '',
  owner: '', currency: 'IDR', balance: 0, balance_idr: 0,
  exchange_rate: 0, market_value_idr: 0,
  quantity: 0, unit_price: 0, cost_basis_idr: 0,
  maturity_date: '', coupon_rate: 0, last_appraised_date: '', due_date: '', notes: '',
}
const form = ref({ ...FORM_DEFAULTS })

// ── Format helpers ────────────────────────────────────────────────────────────
function fmt(n) { return formatIDR(n ?? 0) }

// Format foreign-currency amounts with their own locale convention
function fmtForeign(n, currency) {
  if (n == null || n === 0) return '0'
  try {
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(n)
  } catch { return String(n) }
}

// Format exchange rate compactly, e.g. 16,305.50
function fmtRate(rate) {
  if (!rate || rate <= 0) return '—'
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(rate)
}

function fmtDateChip(d) {
  if (!d) return ''
  const [y, m] = d.split('-')
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${MONTHS[parseInt(m, 10) - 1]} ${y}`
}

function monthKey(d) {
  return (d || '').slice(0, 7)
}

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
function formatLiabType(t) {
  return { mortgage: 'Mortgage', personal_loan: 'Personal Loan', credit_card: 'Credit Card', taxes_owed: 'Taxes Owed', other: 'Other' }[t] || t
}

// ── Month navigation ──────────────────────────────────────────────────────────
// snapshotDates is sorted DESC (newest first); index 0 = most recent
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

// ── Month chip helpers ────────────────────────────────────────────────────────
function selectDate(d) {
  snapshotDate.value = d
  loadItems()
}

// Convert "YYYY-MM" → last day of that month as "YYYY-MM-DD"
function lastDayOfMonth(yyyyMM) {
  if (!yyyyMM) return ''
  const [y, m] = yyyyMM.split('-').map(Number)
  const lastDay = new Date(y, m, 0).getDate()
  return `${y}-${String(m).padStart(2,'0')}-${String(lastDay).padStart(2,'0')}`
}

function pickMonth(val) {
  if (!val) { showMonthPicker.value = false; return }
  const existing = snapshotDates.value.find(d => monthKey(d) === val)
  if (existing) {
    showMonthPicker.value = false
    newMonthInput.value = ''
    snapshotDate.value = existing
    loadItems()
    return
  }
  const dateStr = lastDayOfMonth(val)
  if (!dateStr) return
  showMonthPicker.value = false
  newMonthInput.value   = ''
  // Add to chip list if not already present
  if (!snapshotDates.value.includes(dateStr)) {
    snapshotDates.value = [dateStr, ...snapshotDates.value].sort().reverse()
  }
  snapshotDate.value = dateStr
  loadItems()
}

// ── Filtered lists ────────────────────────────────────────────────────────────
const filteredBalances         = computed(() => balances.value)
const filteredInvestments      = computed(() => holdings.value.filter(h => h.asset_group === 'Investments'))
const filteredBonds            = computed(() => filteredInvestments.value.filter(h => h.asset_class === 'bond'))
const filteredOtherInvestments = computed(() => filteredInvestments.value.filter(h => h.asset_class !== 'bond'))
const filteredRealEstate       = computed(() => holdings.value.filter(h => h.asset_group === 'Real Estate'))
const filteredPhysical         = computed(() => holdings.value.filter(h => h.asset_group === 'Physical Assets'))
const filteredLiabilities      = computed(() => liabilities.value)

const totals = computed(() => ({
  liquid:      filteredBalances.value.reduce((s, b) => s + (b.balance_idr || 0), 0),
  investments: filteredInvestments.value.reduce((s, h) => s + (h.market_value_idr || 0), 0),
  realestate:  filteredRealEstate.value.reduce((s, h) => s + (h.market_value_idr || 0), 0),
  physical:    filteredPhysical.value.reduce((s, h) => s + (h.market_value_idr || 0), 0),
  liabilities: filteredLiabilities.value.reduce((s, l) => s + (l.balance_idr || 0), 0),
}))

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadItems() {
  if (!snapshotDate.value) return
  loading.value   = true
  loadError.value = null
  try {
    const [bals, holds, liabs] = await Promise.all([
      api.getBalances({ snapshot_date: snapshotDate.value }),
      api.getHoldings({ snapshot_date: snapshotDate.value }),
      api.getLiabilities({ snapshot_date: snapshotDate.value }),
    ])
    balances.value    = bals
    holdings.value    = holds
    liabilities.value = liabs
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

// ── CRUD ──────────────────────────────────────────────────────────────────────
async function deleteItem(type, id) {
  if (!confirm('Delete this entry?')) return
  try {
    if (type === 'balance')   await api.deleteBalance(id)
    if (type === 'holding')   await api.deleteHolding(id)
    if (type === 'liability') await api.deleteLiability(id)
    showToast('Deleted')
    await loadItems()
  } catch (e) {
    showToast('Delete failed: ' + e.message)
  }
}

function openForm(mode) {
  form.value      = { ...FORM_DEFAULTS }
  formError.value = ''
  editingId.value = null
  if (activeTab.value === 'cash') formMode.value = 'balance'
  else if (activeTab.value === 'liabilities') formMode.value = 'liability'
  else formMode.value = 'holding'
  showForm.value = true
}

function editItem(type, item) {
  formError.value = ''
  editingId.value = item.id
  if (type === 'balance') {
    formMode.value = 'balance'
    form.value = {
      ...FORM_DEFAULTS,
      institution:  item.institution  || '',
      account:      item.account      || '',
      account_type: item.account_type || 'savings',
      owner:        item.owner        || '',
      currency:     item.currency     || 'IDR',
      balance:      item.balance      || 0,
      balance_idr:  item.balance_idr  || 0,
      exchange_rate: item.exchange_rate || 0,
      notes:        item.notes        || '',
    }
  } else if (type === 'holding') {
    formMode.value = 'holding'
    form.value = {
      ...FORM_DEFAULTS,
      asset_class:          item.asset_class          || 'real_estate',
      asset_name:           item.asset_name           || '',
      isin_or_code:         item.isin_or_code         || '',
      institution:          item.institution          || '',
      owner:                item.owner                || '',
      currency:             item.currency             || 'IDR',
      market_value_idr:     item.market_value_idr     || 0,
      quantity:             item.quantity             || 0,
      unit_price:           item.unit_price           || 0,
      cost_basis_idr:       item.cost_basis_idr       || 0,
      maturity_date:        item.maturity_date        || '',
      coupon_rate:          item.coupon_rate          || 0,
      last_appraised_date:  item.last_appraised_date  || '',
      notes:                item.notes                || '',
    }
  } else {
    formMode.value = 'liability'
    form.value = {
      ...FORM_DEFAULTS,
      liability_type: item.liability_type || 'credit_card',
      liability_name: item.liability_name || '',
      institution:    item.institution    || '',
      owner:          item.owner          || '',
      balance_idr:    item.balance_idr    || 0,
      due_date:       item.due_date       || '',
      notes:          item.notes          || '',
    }
  }
  showForm.value = true
}

async function saveForm() {
  formError.value = ''
  saving.value    = true
  try {
    const sd = snapshotDate.value
    if (formMode.value === 'balance') {
      if (!form.value.institution || !form.value.account) throw new Error('Institution and Account are required')
      const isIDR = form.value.currency === 'IDR'
      await api.upsertBalance({
        snapshot_date: sd, institution: form.value.institution,
        account: form.value.account, account_type: form.value.account_type,
        owner: form.value.owner,
        currency: form.value.currency,
        balance: isIDR ? form.value.balance_idr : form.value.balance,
        balance_idr: form.value.balance_idr,
        exchange_rate: isIDR ? 1.0 : (form.value.exchange_rate || 0),
        notes: form.value.notes,
      })
    } else if (formMode.value === 'holding') {
      if (!form.value.asset_name) throw new Error('Asset name is required')
      await api.upsertHolding({
        snapshot_date: sd, asset_class: form.value.asset_class,
        asset_name: form.value.asset_name, isin_or_code: form.value.isin_or_code,
        institution: form.value.institution, owner: form.value.owner,
        market_value: form.value.market_value_idr, market_value_idr: form.value.market_value_idr,
        quantity: form.value.quantity, unit_price: form.value.unit_price,
        cost_basis: form.value.cost_basis_idr, cost_basis_idr: form.value.cost_basis_idr,
        unrealised_pnl_idr: form.value.market_value_idr - form.value.cost_basis_idr,
        maturity_date: form.value.maturity_date, coupon_rate: form.value.coupon_rate,
        last_appraised_date: form.value.last_appraised_date,
        notes: form.value.notes,
      })
    } else {
      if (!form.value.liability_name) throw new Error('Liability name is required')
      await api.upsertLiability({
        snapshot_date: sd, liability_type: form.value.liability_type,
        liability_name: form.value.liability_name, institution: form.value.institution,
        owner: form.value.owner, balance: form.value.balance_idr,
        balance_idr: form.value.balance_idr, due_date: form.value.due_date,
        notes: form.value.notes,
      })
    }
    showForm.value  = false
    editingId.value = null
    showToast('Saved ✓')
    await loadItems()
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
  }
}

async function generateSnapshot() {
  generating.value = true
  snapMsg.value    = ''
  try {
    await api.createSnapshot({ snapshot_date: snapshotDate.value })
    snapMsg.value   = `✓ Snapshot saved for ${fmtDateChip(snapshotDate.value)}`
    snapMsgOk.value = true
  } catch (e) {
    snapMsg.value   = 'Error: ' + e.message
    snapMsgOk.value = false
  } finally {
    generating.value = false
  }
}

function showToast(msg) {
  toast.value = msg
  setTimeout(() => { toast.value = '' }, 2500)
}

// ── Init ──────────────────────────────────────────────────────────────────────
onMounted(async () => {
  // Load available months from union endpoint
  try {
    snapshotDates.value = collapseMonthDates(await api.wealthSnapshotDates())
    // Default to the most recent month with data
    if (snapshotDates.value.length) {
      snapshotDate.value = snapshotDates.value[0]
    }
  } catch (_) {
    // Fall back to today
    const now = new Date()
    snapshotDate.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
  }
  await loadItems()
})
</script>

<style scoped>
/* ── Group tab bar ───────────────────────────────────────────────────────────  */
.group-tabs-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  padding: 12px 16px 0;
  scrollbar-width: none;
}
.group-tabs-wrap::-webkit-scrollbar { display: none; }
.group-tabs { display: flex; gap: 8px; width: max-content; }
.group-tab {
  padding: 5px 12px;
  border-radius: 20px;
  border: 1.5px solid var(--border);
  background: var(--card);
  color: var(--neutral);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.group-tab.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

/* ── Month navigation centre slot ────────────────────────────────────────────  */
.month-nav-center {
  display: flex;
  align-items: center;
  gap: 8px;
}
/* Small (+) button that opens the month picker */
.nav-btn-sm {
  width: 26px;
  height: 26px;
  font-size: 16px;
  padding: 0;
  line-height: 1;
  border-radius: 50%;
  border: 1.5px dashed var(--primary);
  background: transparent;
  color: var(--primary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.nav-btn-sm:hover { background: var(--primary-dim); }
.month-picker-inline {
  border: 1.5px solid var(--primary);
  border-radius: 20px;
  padding: 4px 10px;
  font-size: 13px;
  background: var(--card);
  color: var(--text);
  outline: none;
  height: 32px;
}

/* ── Section headers ─────────────────────────────────────────────────────────  */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 6px;
  font-size: 13px;
  font-weight: 700;
  color: var(--primary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.section-header-liab { color: #dc2626; }
.section-total { font-size: 14px; font-weight: 700; color: var(--text); }

/* ── Asset list items ────────────────────────────────────────────────────────  */
.asset-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--card);
  transition: background 0.1s;
}
.asset-item:active { background: var(--primary-dim); }
.asset-item-tappable { cursor: pointer; }
.asset-item-tappable:hover { background: var(--primary-dim); }
.asset-main   { flex: 1; min-width: 0; }
.asset-name   { display: block; font-size: 14px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-sub    { display: block; font-size: 12px; color: var(--neutral); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.asset-right  { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; flex-shrink: 0; }
.asset-value  { font-size: 14px; font-weight: 700; color: var(--text); }
.asset-fx     { font-size: 11px; color: var(--neutral); font-variant-numeric: tabular-nums; }
/* Bond sub-group ─────────────────────────────────────────────────────────── */
.sub-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 6px 16px; margin: 4px 0 0;
  font-size: 12px; font-weight: 600;
  color: var(--neutral); letter-spacing: .03em;
  border-left: 3px solid var(--primary, #3b82f6);
}
.sub-total { font-variant-numeric: tabular-nums; }

/* Name + price badge on same row */
.asset-name-row { display: flex; align-items: center; gap: 6px; }

/* Market price badge — green = premium (>100%), red = discount (<100%) */
.price-badge {
  font-size: 11px; font-weight: 700;
  padding: 1px 6px; border-radius: 4px;
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.price-badge.premium  { background: #dcfce7; color: #16a34a; }
.price-badge.discount { background: #fee2e2; color: #dc2626; }
.asset-del {
  flex-shrink: 0;
  width: 28px; height: 28px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--neutral);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.1s, color 0.1s;
}
.asset-del:hover { background: var(--expense-bg); color: var(--expense); }

.empty-state-inline { padding: 10px 16px; font-size: 13px; color: var(--text-muted); }

/* ── Snapshot message ────────────────────────────────────────────────────────  */
.snap-msg {
  text-align: center;
  font-size: 13px;
  margin-top: 8px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
}
.snap-msg.ok  { background: var(--income-bg); color: #15803d; }
.snap-msg.err { background: var(--expense-bg); color: #b91c1c; }

/* ── FAB ─────────────────────────────────────────────────────────────────────  */
.holdings-fab {
  position: fixed;
  bottom: calc(var(--nav-h) + var(--safe-bottom) + 16px);
  right: max(16px, calc(50vw - 214px));
  width: 52px; height: 52px;
  border-radius: 50%;
  border: none;
  background: var(--primary);
  color: #fff;
  font-size: 28px;
  font-weight: 300;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-md);
  z-index: 10;
  transition: background 0.15s;
}
.holdings-fab:active { background: var(--primary-deep); }

/* ── Modal ───────────────────────────────────────────────────────────────────  */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.45);
  z-index: 100;
  display: flex;
  align-items: flex-end;
}
.modal-sheet {
  background: var(--card);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  width: 100%;
  max-width: 460px;
  margin: 0 auto;
  max-height: 88dvh;
  overflow-y: auto;
  padding-bottom: calc(var(--safe-bottom) + 12px);
}
.modal-header {
  display: flex;
  align-items: center;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--border);
}
.modal-title  { flex: 1; font-size: 16px; font-weight: 700; }
.modal-close  {
  width: 32px; height: 32px;
  border: none; background: var(--bg); border-radius: 50%;
  font-size: 16px; cursor: pointer; color: var(--neutral);
  display: flex; align-items: center; justify-content: center;
}

/* ── Form type selector ──────────────────────────────────────────────────────  */
.form-type-tabs {
  display: flex;
  gap: 0;
  padding: 12px 16px 8px;
}
.form-type-tab {
  flex: 1;
  padding: 7px 4px;
  border: 1.5px solid var(--border);
  background: var(--card);
  color: var(--neutral);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s;
}
.form-type-tab:first-child { border-radius: var(--radius-sm) 0 0 var(--radius-sm); }
.form-type-tab:last-child  { border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.form-type-tab + .form-type-tab { border-left: none; }
.form-type-tab.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}

/* ── Form body ───────────────────────────────────────────────────────────────  */
.form-body {
  padding: 4px 16px 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.form-body label {
  font-size: 12px;
  font-weight: 600;
  color: var(--neutral);
  margin-top: 8px;
}
.form-body input,
.form-body select {
  border: 1.5px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  font-size: 14px;
  background: var(--bg);
  color: var(--text);
  outline: none;
  width: 100%;
}
.form-body input:focus,
.form-body select:focus { border-color: var(--primary); background: #fff; }
</style>
