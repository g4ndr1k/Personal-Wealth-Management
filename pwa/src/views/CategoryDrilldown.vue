<template>
  <div>
    <!-- Sub-page header (back + title) -->
    <div class="drill-header">
      <button class="back-btn" @click="goBack">
        <span class="back-arrow">‹</span>
      </button>
      <div class="drill-title-block">
        <!-- Breadcrumb when coming from a group -->
        <div v-if="fromGroup" class="drill-breadcrumb" @click="goBack">
          {{ groupIcon }} {{ fromGroup }} ›
        </div>
        <div class="drill-title">
          <span v-if="catMeta">{{ catMeta.icon }}</span>
          {{ category }}
        </div>
        <div class="drill-subtitle">{{ monthLabel }}</div>
      </div>
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

    <template v-else>
      <!-- Summary bar -->
      <div class="drill-summary card">
        <div class="ds-item">
          <div class="ds-label">Total Spent</div>
          <div class="ds-value text-expense">{{ fmt(Math.abs(totalAmount)) }}</div>
        </div>
        <div class="ds-divider"></div>
        <div class="ds-item">
          <div class="ds-label">Transactions</div>
          <div class="ds-value text-neutral">{{ transactions.length }}</div>
        </div>
        <div v-if="editedCount > 0" class="ds-divider"></div>
        <div v-if="editedCount > 0" class="ds-item">
          <div class="ds-label">Edited</div>
          <div class="ds-value" style="color:var(--income)">{{ editedCount }}</div>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="!transactions.length" class="empty-state">
        <div class="e-icon">📭</div>
        <div class="e-msg">No transactions found</div>
        <div class="e-sub">No {{ category }} spending in {{ monthLabel }}</div>
      </div>

      <!-- Transaction list -->
      <div v-else class="tx-list">
        <template v-for="tx in transactions" :key="tx.hash">
          <!-- Row -->
          <div
            class="tx-row"
            :class="{ expanded: expandedHash === tx.hash, edited: editedHashes.has(tx.hash) }"
            @click="toggle(tx)"
          >
            <div class="tx-main">
              <div class="tx-merchant">
                {{ tx.merchant || tx.raw_description }}
                <span v-if="editedHashes.has(tx.hash)" class="edited-badge">✓</span>
              </div>
              <div class="tx-cat">
                <span>{{ catIcon(tx.category) }} {{ tx.category || '—' }}</span>
                · {{ tx.owner }} · {{ tx.date }}
              </div>
            </div>
            <div class="tx-right">
              <div class="tx-amount text-expense">{{ fmt(Math.abs(tx.amount)) }}</div>
              <div class="tx-chevron" :class="{ open: expandedHash === tx.hash }">›</div>
            </div>
          </div>

          <!-- Expanded detail + edit panel -->
          <div v-if="expandedHash === tx.hash" class="tx-detail-panel">
            <!-- Detail grid -->
            <div class="detail-grid">
              <div class="detail-item">
                <div class="dk">Raw description</div>
                <div class="dv mono">{{ tx.raw_description }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Institution</div>
                <div class="dv">{{ tx.institution || '—' }}</div>
              </div>
              <div class="detail-item">
                <div class="dk">Account</div>
                <div class="dv">{{ tx.account || '—' }}</div>
              </div>
              <div v-if="tx.original_currency" class="detail-item" style="grid-column:1/-1">
                <div class="dk">Foreign amount</div>
                <div class="dv">{{ tx.original_amount }} {{ tx.original_currency }} @ {{ tx.exchange_rate }}</div>
              </div>
              <div v-if="tx.notes" class="detail-item" style="grid-column:1/-1">
                <div class="dk">Notes</div>
                <div class="dv">{{ tx.notes }}</div>
              </div>
            </div>

            <!-- Edit form -->
            <div class="edit-form">
              <div class="edit-form-hd">✏️ Edit Transaction</div>

              <!-- Merchant name -->
              <div class="form-row">
                <label class="form-label">Merchant name</label>
                <input
                  class="form-input"
                  v-model="form.merchant"
                  placeholder="e.g. Grab Food, IKEA, Indomaret…"
                  @click.stop
                  @keyup.enter="save(tx)"
                />
              </div>

              <!-- Category -->
              <div class="form-row">
                <label class="form-label">Category</label>
                <select class="form-input" v-model="form.category" @click.stop>
                  <option value="">— select —</option>
                  <option v-for="c in store.categoryNames" :key="c" :value="c">
                    {{ store.categoryMap[c]?.icon || '' }} {{ c }}
                  </option>
                </select>
              </div>

              <!-- Notes -->
              <div class="form-row">
                <label class="form-label">Notes <span style="font-weight:400;color:var(--text-muted)">(optional)</span></label>
                <input
                  class="form-input"
                  v-model="form.notes"
                  placeholder="Any additional notes…"
                  @click.stop
                />
              </div>

              <!-- Match type -->
              <div class="form-row">
                <label class="form-label">Alias match type</label>
                <div class="radio-group">
                  <label class="radio-label" @click.stop>
                    <input type="radio" v-model="form.match_type" value="exact" @click.stop /> Exact
                  </label>
                  <label class="radio-label" @click.stop>
                    <input type="radio" v-model="form.match_type" value="contains" @click.stop /> Contains
                  </label>
                  <label class="radio-label" @click.stop>
                    <input type="radio" v-model="form.match_type" value="regex" @click.stop /> Regex
                  </label>
                </div>
              </div>

              <!-- Apply to similar -->
              <div class="form-row">
                <label class="check-label" @click.stop>
                  <input type="checkbox" v-model="form.apply_to_similar" @click.stop />
                  Apply to all similar transactions
                  <span v-if="similarCount(tx) > 1" class="similar-hint">
                    ({{ similarCount(tx) }} with same description)
                  </span>
                </label>
              </div>

              <!-- Success / Error feedback -->
              <div v-if="saveSuccess" class="alert alert-success" style="margin:6px 0;font-size:12px">
                ✅ {{ saveSuccess }}
              </div>
              <div v-if="saveError" class="alert alert-error" style="margin:6px 0;font-size:12px">
                ❌ {{ saveError }}
              </div>

              <!-- Actions -->
              <div class="form-actions">
                <button
                  class="btn btn-primary"
                  :disabled="!form.merchant || !form.category || saving"
                  @click.stop="save(tx)"
                >
                  <span v-if="saving">
                    <span class="spinner" style="width:12px;height:12px;border-width:2px"></span>
                  </span>
                  <span v-else>💾 Save &amp; Update Alias</span>
                </button>
                <button class="btn btn-ghost" @click.stop="expandedHash = null">
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const router = useRouter()
const route  = useRoute()
const store  = useFinanceStore()

// ── Route params ────────────────────────────────────────────────────────────
const category  = route.query.category || ''
const year      = Number(route.query.year)  || new Date().getFullYear()
const month     = Number(route.query.month) || new Date().getMonth() + 1
const owner     = route.query.owner || ''
const fromGroup = route.query.fromGroup ? decodeURIComponent(route.query.fromGroup) : ''

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
const groupIcon = computed(() => GROUP_ICONS[fromGroup] || '📁')

// ── State ───────────────────────────────────────────────────────────────────
const transactions = ref([])
const loading      = ref(false)
const error        = ref(null)
const expandedHash = ref(null)
const editedHashes = ref(new Set())
const editedCount  = ref(0)
const saving       = ref(false)
const saveError    = ref(null)
const saveSuccess  = ref('')

const form = ref({
  merchant:         '',
  category:         '',
  notes:            '',
  match_type:       'exact',
  apply_to_similar: true,
})

// ── Computed ────────────────────────────────────────────────────────────────
const MONTHS_LONG = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December']

const monthLabel = computed(() => `${MONTHS_LONG[month - 1]} ${year}`)

const catMeta = computed(() => store.categoryMap[category] || null)

const totalAmount = computed(() =>
  transactions.value.reduce((s, tx) => s + tx.amount, 0)
)

// ── Helpers ─────────────────────────────────────────────────────────────────
function fmt(n) {
  return formatIDR(n)
}

function catIcon(name) {
  return store.categoryMap[name]?.icon || '📁'
}

function titleCase(str) {
  return (str || '').toLowerCase().replace(/(?:^|\s)\S/g, c => c.toUpperCase())
}

/** Count how many visible transactions share the same raw_description */
function similarCount(tx) {
  return transactions.value.filter(t => t.raw_description === tx.raw_description).length
}

// ── Navigation ───────────────────────────────────────────────────────────────
function goBack() {
  // If we arrived from a group drilldown, go back properly via router.back()
  // so the group view is restored from history (preserving its byCategory data).
  if (window.history.length > 2) {
    router.back()
  } else {
    router.push('/')
  }
}

// ── Toggle row expand ───────────────────────────────────────────────────────
function toggle(tx) {
  saveError.value   = null
  saveSuccess.value = ''

  if (expandedHash.value === tx.hash) {
    expandedHash.value = null
    return
  }
  expandedHash.value = tx.hash

  // Pre-fill the edit form
  form.value = {
    merchant:         tx.merchant || titleCase(tx.raw_description),
    category:         tx.category || category,
    notes:            tx.notes    || '',
    match_type:       'exact',
    apply_to_similar: true,
  }
}

// ── Save ────────────────────────────────────────────────────────────────────
async function save(tx) {
  if (!form.value.merchant || !form.value.category) return
  saving.value      = true
  saveError.value   = null
  saveSuccess.value = ''

  try {
    // Use saveAlias — sets merchant canonical name, writes to Merchant Aliases
    // tab, and batch-updates all matching transactions in the Transactions tab.
    const result = await api.saveAlias({
      hash:             tx.hash,
      alias:            tx.raw_description,
      merchant:         form.value.merchant,
      category:         form.value.category,
      match_type:       form.value.match_type,
      apply_to_similar: form.value.apply_to_similar,
    })

    // If notes were provided, also patch via patchCategory to persist notes
    // to the Category Overrides sheet (notes are not part of the alias model).
    if (form.value.notes && form.value.notes !== tx.notes) {
      await api.patchCategory(tx.hash, {
        category:     form.value.category,
        notes:        form.value.notes,
        update_alias: false,  // alias already saved above
      })
      tx.notes = form.value.notes
    }

    // ── Local state update ─────────────────────────────────────────────────
    const updatedCount = result.updated_count ?? 1

    if (form.value.apply_to_similar) {
      // Update all visible rows that share the same raw_description
      const desc = tx.raw_description
      for (const t of transactions.value) {
        if (t.raw_description === desc) {
          t.merchant  = form.value.merchant
          t.category  = form.value.category
          editedHashes.value.add(t.hash)
        }
      }
    } else {
      tx.merchant  = form.value.merchant
      tx.category  = form.value.category
      editedHashes.value.add(tx.hash)
    }
    editedCount.value = editedHashes.value.size

    saveSuccess.value = updatedCount > 1
      ? `Saved! Also updated ${updatedCount - 1} similar transaction${updatedCount - 1 !== 1 ? 's' : ''}.`
      : 'Saved successfully.'

    // Auto-close the panel after a short delay on success
    setTimeout(() => {
      expandedHash.value = null
      saveSuccess.value  = ''
    }, 2200)

  } catch (e) {
    saveError.value = e.message
  } finally {
    saving.value = false
  }
}

// ── Data loading ────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value   = null
  try {
    const params = {
      year,
      month,
      category,
      limit:  500,   // All transactions for a single category/month
      offset: 0,
    }
    if (owner) params.owner = owner

    const res = await api.transactions(params)
    transactions.value = res.transactions || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
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
  width: 38px;
  height: 38px;
  border-radius: 12px;
  border: 1.5px solid var(--border);
  background: var(--card);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s, border-color 0.15s;
  box-shadow: var(--shadow);
}
.back-btn:active { background: var(--primary-dim); border-color: var(--primary); }

.back-arrow {
  font-size: 20px;
  color: var(--primary);
  line-height: 1;
  margin-top: -1px;
}

.drill-title-block {
  min-width: 0;
}
.drill-breadcrumb {
  font-size: 11px;
  color: var(--primary-light);
  font-weight: 700;
  margin-bottom: 2px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 3px;
}
.drill-breadcrumb:active { opacity: 0.7; }

.drill-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.4px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.drill-subtitle {
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 600;
  margin-top: 1px;
}

/* ── Summary bar ─────────────────────────────────────────────────────────── */
.drill-summary {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.ds-item {
  flex: 1;
  text-align: center;
}
.ds-label {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: var(--text-muted);
  margin-bottom: 3px;
}
.ds-value {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.3px;
}
.ds-divider {
  width: 1px;
  height: 32px;
  background: var(--border);
  margin: 0 8px;
  flex-shrink: 0;
}

/* ── Transaction rows ────────────────────────────────────────────────────── */
.tx-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tx-row {
  display: flex;
  align-items: center;
  padding: 11px 14px;
  background: var(--card);
  border-radius: var(--radius);
  border: 1px solid rgba(226,232,240,0.85);
  cursor: pointer;
  transition: background 0.12s;
  gap: 10px;
  box-shadow: var(--shadow);
}
.tx-row:active { background: var(--primary-dim); }
.tx-row.expanded {
  border-bottom-left-radius: 0;
  border-bottom-right-radius: 0;
  border-bottom-color: transparent;
  background: var(--primary-dim);
}
.tx-row.edited {
  border-left: 3px solid var(--income);
}

.tx-main { flex: 1; min-width: 0; }
.tx-merchant {
  font-size: 13px;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: flex;
  align-items: center;
  gap: 5px;
}
.tx-cat {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 2px;
}

.tx-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  flex-shrink: 0;
}
.tx-amount {
  font-size: 13px;
  font-weight: 700;
}
.tx-chevron {
  font-size: 14px;
  color: var(--text-muted);
  transition: transform 0.2s;
}
.tx-chevron.open { transform: rotate(90deg); }

.edited-badge {
  font-size: 10px;
  font-weight: 700;
  color: var(--income);
  background: var(--income-bg);
  border-radius: 4px;
  padding: 1px 4px;
}

/* ── Expanded detail + edit panel ────────────────────────────────────────── */
.tx-detail-panel {
  background: var(--card);
  border: 1px solid rgba(226,232,240,0.85);
  border-top: none;
  border-radius: 0 0 var(--radius) var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow);
  margin-bottom: 2px;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border);
}
.dk {
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 2px;
}
.dv {
  font-size: 12px;
  color: var(--text);
  word-break: break-all;
}
.dv.mono {
  font-family: ui-monospace, monospace;
  font-size: 10px;
}

/* ── Edit form ───────────────────────────────────────────────────────────── */
.edit-form {
  padding: 14px;
  background: var(--bg);
}
.edit-form-hd {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
}

.form-row {
  margin-bottom: 10px;
}
.form-label {
  display: block;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}
.form-input {
  width: 100%;
  padding: 8px 10px;
  border: 1.5px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  background: var(--card);
  color: var(--text);
  font-family: inherit;
  transition: border-color 0.15s;
  appearance: none;
}
.form-input:focus {
  outline: none;
  border-color: var(--primary-light);
}

.radio-group {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 2px;
}
.radio-label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}

.check-label {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text);
}
.check-label input[type="checkbox"] {
  margin-top: 2px;
  flex-shrink: 0;
  width: 15px;
  height: 15px;
}
.similar-hint {
  color: var(--text-muted);
  font-size: 11px;
}

.form-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

/* ── Buttons (local scope mirrors global) ────────────────────────────────── */
.btn {
  padding: 9px 16px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  border: none;
  font-family: inherit;
  transition: opacity 0.15s, background 0.15s;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.btn:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-primary {
  background: var(--primary);
  color: #fff;
  flex: 1;
}
.btn-primary:not(:disabled):active { background: var(--primary-deep); }

.btn-ghost {
  background: transparent;
  color: var(--neutral);
  border: 1.5px solid var(--border);
}
.btn-ghost:active { background: var(--primary-dim); }

/* ── Alerts ──────────────────────────────────────────────────────────────── */
.alert { border-radius: 8px; padding: 9px 12px; font-size: 13px; display: flex; align-items: center; gap: 6px; }
.alert-error   { background: var(--expense-bg); color: var(--expense); border: 1px solid rgba(239,68,68,0.18); }
.alert-success { background: var(--income-bg);  color: #15803d;        border: 1px solid rgba(34,197,94,0.25); }

/* ── Loading / empty ─────────────────────────────────────────────────────── */
.loading { display: flex; align-items: center; gap: 10px; color: var(--text-muted); padding: 24px 0; justify-content: center; }
.spinner {
  width: 18px; height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.empty-state { text-align: center; padding: 40px 20px; }
.e-icon { font-size: 36px; margin-bottom: 8px; }
.e-msg  { font-size: 15px; font-weight: 700; color: var(--text); }
.e-sub  { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
</style>
