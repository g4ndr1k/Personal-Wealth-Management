<template>
  <div :class="['settings-page', { 'settings-page--desktop': isDesktop }]">
    <div v-if="!isDesktop" class="section-hd">
      <span class="settings-head-icon" v-html="NAV_SVGS.CoreTax"></span> CoreTax SPT
    </div>

    <nav v-if="isDesktop" class="settings-sub-nav">
      <div class="settings-sub-nav__title">CoreTax SPT {{ store.taxYear }}</div>
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="settings-sub-nav__item"
        :class="{ 'is-active': activeTab === tab.id }"
        @click="activeTab = tab.id"
      >
        <span class="settings-sub-nav__icon" v-html="tab.icon"></span>
        <span>{{ tab.label }}</span>
      </button>
    </nav>

    <div class="settings-content">
      <div v-if="isDesktop" class="section-hd">
        <span class="settings-head-icon" v-html="NAV_SVGS.CoreTax"></span>
        CoreTax SPT
        <select class="year-select" :value="store.taxYear" @change="switchYear($event.target.value)">
          <option v-for="y in yearOptions" :key="y" :value="y">{{ y }}</option>
        </select>
        <span v-if="store.summary" class="coverage-chip" :class="{ ok: store.coveragePct >= 80 }">
          {{ store.filledRows }}/{{ store.totalRows }} filled · {{ store.coveragePct }}%
        </span>
      </div>

      <!-- Mobile year selector -->
      <div v-if="!isDesktop" class="setting-card">
        <div class="setting-row">
          <select class="range-select" :value="store.taxYear" @change="switchYear($event.target.value)">
            <option v-for="y in yearOptions" :key="y" :value="y">{{ y }}</option>
          </select>
        </div>
      </div>

      <div :key="isDesktop ? activeTab : 'all'" class="settings-grid">

        <!-- ═══ TAB 1: Import ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'import'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Audit"></span> Import Previous SPT
          </div>
          <div class="setting-desc">Upload the prior-year SPT XLSX to seed the tax ledger for {{ store.taxYear }}.</div>
          <div v-if="store.hasRows" class="alert alert-warn" style="margin-top:10px">
            Rows already exist for {{ store.taxYear }}. Importing will add missing rows.
          </div>
          <div class="setting-row" style="margin-top:12px">
            <input type="file" ref="fileInput" accept=".xlsx" @change="onFileSelected" class="file-input" />
            <button class="btn" @click="uploadFile" :disabled="!selectedFile || store.loading">
              {{ store.loading ? 'Importing…' : 'Upload & Parse' }}
            </button>
          </div>
          <div v-if="importResult" class="alert alert-success" style="margin-top:10px">
            Parsed {{ importResult.row_count }} rows (batch {{ importResult.batch_id.slice(0,8) }}…).
            <span v-if="importResult.warnings.length">Warnings: {{ importResult.warnings.join('; ') }}</span>
          </div>

          <!-- Staging preview -->
          <div v-if="store.staging.length" style="margin-top:14px">
            <div class="setting-title" style="font-size:14px">Staging Preview</div>
            <div class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Row</th><th>Code</th><th>Acq Year</th><th>Description</th><th class="th-prior">Prior ({{ store.taxYear }})</th><th>Carry?</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in store.staging" :key="row.id">
                    <td>{{ row.source_row_no }}</td>
                    <td>{{ row.parsed_kode_harta }}</td>
                    <td>{{ row.parsed_acquisition_year }}</td>
                    <td class="cell-desc">{{ row.parsed_keterangan }}</td>
                    <td class="cell-num">{{ fmtNum(row.parsed_carry_amount_idr) }}</td>
                    <td>
                      <input type="checkbox"
                        :checked="row.user_override_carry_forward ?? row.rule_default_carry_forward"
                        @change="toggleCarryOverride(row)"
                      />
                    </td>
                  </tr>
                  <tr class="row-total">
                    <td colspan="4"></td>
                    <td class="cell-num">{{ fmtNum(priorTotal) }}</td>
                    <td></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="action-row" style="margin-top:12px">
              <button class="btn" @click="commitImport" :disabled="store.loading">Commit to Ledger</button>
              <button class="btn btn-ghost" @click="discardImport">Discard</button>
            </div>
          </div>
        </div>

        <!-- ═══ TAB 2: Carry Forward Review ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'review'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Dashboard"></span> Carry Forward Review
          </div>
          <div class="setting-desc">Review and edit tax rows. Locked values are preserved during reconcile.</div>
          <div class="action-row" style="margin-top:12px">
            <button class="btn btn-ghost" @click="resetRules" :disabled="store.loading">Reset Unlocked from Rules</button>
          </div>
          <div v-if="store.assetRows.length" class="table-wrap" style="margin-top:14px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Kode</th><th>Description</th><th>{{ store.taxYear }}</th><th>{{ store.taxYear + 1 }}</th><th>Market</th><th>Source</th><th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in store.assetRows" :key="row.id" :class="{ 'row-locked': row.amount_locked }">
                  <td>{{ row.kode_harta }}</td>
                  <td class="cell-desc">{{ row.keterangan }}</td>
                  <td class="cell-num">{{ fmtIdr(row.prior_amount_idr) }}</td>
                  <td class="cell-num editable" @dblclick="editCell(row, 'current_amount_idr')">
                    {{ fmtIdr(row.current_amount_idr) }}
                    <span v-if="row.amount_locked" class="lock-badge">🔒</span>
                  </td>
                  <td class="cell-num">{{ fmtIdr(row.market_value_idr) }}</td>
                  <td class="cell-src">{{ row.current_amount_source }}</td>
                  <td>
                    <button class="btn-icon" @click="toggleLock(row, 'amount')" :title="row.amount_locked ? 'Unlock' : 'Lock'">
                      {{ row.amount_locked ? '🔓' : '🔒' }}
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="setting-desc" style="margin-top:10px">No rows yet. Import a prior-year SPT first.</div>

          <!-- Liabilities -->
          <div v-if="store.liabilityRows.length" style="margin-top:16px">
            <div class="setting-title" style="font-size:14px">Liabilities</div>
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>Type</th><th>Description</th><th>Amount</th></tr></thead>
                <tbody>
                  <tr v-for="row in store.liabilityRows" :key="row.id">
                    <td>{{ row.kode_harta }}</td>
                    <td class="cell-desc">{{ row.keterangan }}</td>
                    <td class="cell-num">{{ fmtIdr(row.current_amount_idr) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- Add row -->
          <div class="action-row" style="margin-top:12px">
            <button class="btn btn-ghost" @click="showAddRow = true">+ Add Row</button>
          </div>
        </div>

        <!-- ═══ TAB 3: Mapping (before Reconcile) ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'mapping'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.CoreTax"></span> Mapping
          </div>
          <div class="setting-desc">Map PWM items to CoreTax rows. Mapping runs before Reconcile.</div>

          <!-- Section 1: Unmapped PWM items (default expanded) -->
          <details open style="margin-top:14px">
            <summary class="section-summary">
              Unmapped PWM Items
              <span v-if="store.unmappedPwm.length" class="badge">{{ store.unmappedPwm.length }}</span>
            </summary>
            <div class="action-row" style="margin-top:8px">
              <button class="btn btn-ghost btn-sm" @click="suggestAll" :disabled="store.loading">Suggest All</button>
            </div>
            <div v-if="store.unmappedPwm.length" class="table-wrap" style="margin-top:8px">
              <table class="data-table">
                <thead>
                  <tr><th>Source</th><th>PWM Item</th><th>Fingerprint</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-for="(um, idx) in store.unmappedPwm" :key="'ump-'+idx">
                    <td class="cell-src">{{ um.source_kind }}</td>
                    <td class="cell-desc">{{ um.pwm_label }}</td>
                    <td class="cell-src" :title="um.fingerprint_raw">{{ um.match_kind }}:{{ um.match_value?.slice(0,12) }}…</td>
                    <td>
                      <button class="btn btn-ghost btn-sm" @click="mapToRow(um)">Map</button>
                      <button class="btn btn-ghost btn-sm" @click="createRowFromUnmapped(um)">Create</button>
                      <button class="btn btn-ghost btn-sm" @click="suggestOne(um)">Suggest</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="setting-desc" style="margin-top:8px">All PWM items are mapped.</div>
          </details>

          <!-- Section 2: Mapped items by CoreTax row (default collapsed) -->
          <details style="margin-top:12px">
            <summary class="section-summary">
              Mapped Items by CoreTax Row
              <span class="badge">{{ store.mappings.length }}</span>
            </summary>
            <div class="action-row" style="margin-top:8px">
              <button class="btn btn-ghost btn-sm" @click="doFindRenames" :disabled="store.loading">Find Renames</button>
            </div>
            <div v-if="store.mappings.length" class="table-wrap" style="margin-top:8px">
              <table class="data-table">
                <thead>
                  <tr><th>Kind</th><th>Fingerprint</th><th>→ Target</th><th>Conf</th><th>Src</th><th>Years</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-for="m in store.mappings" :key="m.id">
                    <td class="cell-src">{{ m.match_kind }}</td>
                    <td class="cell-desc" :title="m.fingerprint_raw">{{ m.match_value?.slice(0,12) }}…</td>
                    <td>{{ m.target_kode_harta }} <span class="cell-src">{{ m.target_stable_key?.slice(0,20) }}…</span></td>
                    <td>
                      <span class="conf-dot" :class="'conf-'+(m.confidence_level||'HIGH').toLowerCase()" :title="m.confidence_score">{{ m.confidence_level || 'HIGH' }}</span>
                    </td>
                    <td class="cell-src">{{ m.source || 'manual' }}</td>
                    <td>{{ m.years_used || 0 }}</td>
                    <td>
                      <button class="btn btn-ghost btn-sm" @click="confirmMappingAction(m)" title="Confirm">✓</button>
                      <button class="btn-icon" @click="deleteMapping(m.id)" title="Delete">✕</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>

          <!-- Section 3: Stale & lifecycle (default collapsed, badge with count) -->
          <details style="margin-top:12px">
            <summary class="section-summary">
              Stale &amp; Lifecycle
              <span class="badge badge-warn">{{ staleCount }}</span>
            </summary>
            <div class="lifecycle-pills" style="margin-top:8px">
              <button v-for="b in lifecycleBucketNames" :key="b"
                class="pill" :class="{ 'is-active': activeBucket === b }"
                @click="activeBucket = activeBucket === b ? null : b">
                {{ b }} <span class="pill-count">{{ (store.lifecycleBuckets[b] || 0) }}</span>
              </button>
            </div>
            <div v-if="lifecycleItems.length" class="table-wrap" style="margin-top:8px">
              <table class="data-table">
                <thead>
                  <tr><th>Kind</th><th>Fingerprint</th><th>→ Target</th><th>Bucket</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-for="m in lifecycleItems" :key="'lc-'+m.id">
                    <td class="cell-src">{{ m.match_kind }}</td>
                    <td class="cell-desc" :title="m.fingerprint_raw">{{ m.match_value?.slice(0,12) }}…</td>
                    <td>{{ m.target_kode_harta }}</td>
                    <td><span class="badge badge-warn">{{ m.lifecycle_bucket }}</span></td>
                    <td>
                      <button class="btn btn-ghost btn-sm" @click="deleteMapping(m.id)">Delete</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="setting-desc" style="margin-top:8px">No stale mappings.</div>
          </details>
        </div>

        <!-- ═══ TAB 4: Reconcile from PWM (simplified) ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'reconcile'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Audit"></span> Reconcile from PWM
          </div>
          <div class="setting-desc">Apply mapped rules to auto-fill rows from PWM data.</div>
          <div class="setting-row setting-row-range reconcile-range-row" style="margin-top:12px">
            <div class="range-field">
              <label class="range-label">FS Start</label>
              <select class="range-select" v-model="fsStart">
                <option v-for="m in monthOptions" :key="'fs-s-'+m" :value="m">{{ fmtMonth(m) }}</option>
              </select>
            </div>
            <div class="range-field">
              <label class="range-label">FS End</label>
              <select class="range-select" v-model="fsEnd">
                <option v-for="m in monthOptions" :key="'fs-e-'+m" :value="m">{{ fmtMonth(m) }}</option>
              </select>
            </div>
          </div>
          <div class="action-row" style="margin-top:12px">
            <button class="btn" @click="runReconcile" :disabled="!fsStart || !fsEnd || store.loading">
              {{ store.loading ? 'Reconciling…' : 'Run Reconcile' }}
            </button>
            <button v-if="store.reconcileRuns.length >= 2" class="btn btn-ghost" @click="compareRuns">Compare to Previous</button>
          </div>

          <!-- LOW-confidence warning banner -->
          <div v-if="lowConfidenceCount > 0" class="alert alert-warn" style="margin-top:10px">
            {{ lowConfidenceCount }} matches used low-confidence rules — review in Mapping tab.
          </div>

          <!-- Reconcile results -->
          <div v-if="reconcileResult" style="margin-top:14px">
            <div class="preview-summary">
              <div class="preview-summary__line ok">✔ {{ reconcileResult.summary?.filled || 0 }} filled</div>
              <div class="preview-summary__line warn">⚠ {{ reconcileResult.summary?.locked_skipped || 0 }} locked (skipped)</div>
              <div class="preview-summary__line warn">⚠ {{ reconcileResult.summary?.unmatched || 0 }} unmatched</div>
              <div v-if="reconcileResult.summary?.legacy_heuristics_enabled" class="preview-summary__line warn">
                Legacy heuristics enabled ({{ reconcileResult.summary?.legacy_matches || 0 }} matches)
              </div>
            </div>
          </div>

          <!-- Matched rows with confidence badges and breakdown -->
          <div v-if="matchedTraces.length" style="margin-top:14px">
            <details open class="preview-summary__line ok">
              <summary>Matched Rows ({{ matchedTraces.length }})</summary>
              <div class="table-wrap" style="margin-top:8px">
                <table class="data-table">
                  <thead>
                    <tr><th>Code</th><th>Description</th><th>PWM Source</th><th>PWM Value</th><th>Tier</th><th>Conf</th><th>Status</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(t, idx) in matchedTraces" :key="'mt-'+idx">
                      <td>{{ t.kode_harta }}</td>
                      <td class="cell-desc">{{ t.keterangan }}</td>
                      <td>{{ t.pwm_source }}</td>
                      <td class="cell-num">{{ fmtNum(t.pwm_value) }}</td>
                      <td class="cell-src">{{ t.tier || '—' }}</td>
                      <td>
                        <span class="conf-dot" :class="'conf-'+(t.confidence_level||'high').toLowerCase()">{{ t.confidence_level || '—' }}</span>
                      </td>
                      <td class="cell-src">
                        {{ t.status === 'filled' ? '✔' : '🔒' }} {{ t.status }}
                        <span v-if="t.warnings.length"> ({{ t.warnings.join(', ') }})</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </div>

          <!-- Run diff results -->
          <div v-if="runDiffResult" style="margin-top:14px">
            <details open class="preview-summary__line warn">
              <summary>Diff vs Run #{{ runDiffResult.previous_run_id }}</summary>
              <div v-if="runDiffResult.added_count || runDiffResult.removed_count" style="margin-top:8px">
                <div class="setting-desc">+{{ runDiffResult.added_count }} added, -{{ runDiffResult.removed_count }} removed</div>
              </div>
              <div v-else class="setting-desc" style="margin-top:8px">No differences.</div>
            </details>
          </div>

          <!-- Reconcile runs history -->
          <details v-if="store.reconcileRuns.length" style="margin-top:12px">
            <summary class="setting-desc" style="cursor:pointer">Run history ({{ store.reconcileRuns.length }})</summary>
            <ul class="run-history">
              <li v-for="run in store.reconcileRuns" :key="run.id">
                #{{ run.id }} — {{ run.created_at?.slice(0,19) }} —
                filled {{ run.summary?.filled }}, unmatched {{ run.summary?.unmatched }}
                <span v-if="run.summary?.tier1_matches"> · T1:{{ run.summary.tier1_matches }}</span>
                <span v-if="run.summary?.tier2_matches"> · T2:{{ run.summary.tier2_matches }}</span>
              </li>
            </ul>
          </details>
        </div>

        <!-- ═══ TAB 5: Export ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'export'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.CoreTax"></span> Export CoreTax XLSX
          </div>
          <div class="setting-desc">Generate the XLSX file from the current ledger state.</div>
          <div v-if="store.summary" class="preview-summary" style="margin-top:12px">
            <div>Prior total: <strong>{{ fmtIdr(store.summary.by_kode?.reduce((s, k) => s + (k.total_prior || 0), 0)) }}</strong></div>
            <div>Current total: <strong>{{ fmtIdr(store.summary.by_kode?.reduce((s, k) => s + (k.total_current || 0), 0)) }}</strong></div>
            <div>Market total: <strong>{{ fmtIdr(store.summary.by_kode?.reduce((s, k) => s + (k.total_market || 0), 0)) }}</strong></div>
          </div>
          <div class="action-row" style="margin-top:12px">
            <button class="btn" @click="doExport" :disabled="!store.hasRows || store.loading">
              {{ store.loading ? 'Exporting…' : 'Export XLSX' }}
            </button>
          </div>
          <div v-if="exportResult" class="alert alert-success" style="margin-top:10px">
            Exported {{ exportResult.file_id }}.
            <a :href="downloadUrl(exportResult.file_id)" class="link" download>Download</a>
            <button class="btn btn-ghost btn-sm" @click="viewAudit(exportResult.file_id)" style="margin-left:8px">View Audit</button>
          </div>

          <!-- Prior exports -->
          <details v-if="store.exports.length" style="margin-top:12px">
            <summary class="setting-desc" style="cursor:pointer">Recent Exports ({{ store.exports.length }})</summary>
            <ul class="run-history" style="margin-top:8px">
              <li v-for="exp in store.exports" :key="exp.file_id">
                {{ exp.file_id }} — {{ exp.created_at?.slice(0,19) }}
                <a :href="downloadUrl(exp.file_id)" class="link" download style="margin-left:8px">Download</a>
              </li>
            </ul>
          </details>
        </div>

        <!-- Add Row Modal -->
        <div v-if="showAddRow" class="modal-overlay" @click.self="showAddRow = false">
          <div class="modal-card">
            <div class="setting-title">Add Manual Row</div>
            <div class="form-grid">
              <label class="range-label">Kind</label>
              <select class="range-select" v-model="newRow.kind">
                <option value="asset">Asset</option>
                <option value="liability">Liability</option>
              </select>
              <label class="range-label">Kode Harta</label>
              <input class="range-select" v-model="newRow.kode_harta" placeholder="e.g. 012" />
              <label class="range-label">Description</label>
              <input class="range-select" v-model="newRow.keterangan" />
              <label class="range-label">Owner</label>
              <input class="range-select" v-model="newRow.owner" />
              <label class="range-label">Acq. Year</label>
              <input class="range-select" type="number" v-model.number="newRow.acquisition_year" />
              <label class="range-label">Current Amount (IDR)</label>
              <input class="range-select" type="number" v-model.number="newRow.current_amount_idr" />
            </div>
            <div class="action-row" style="margin-top:16px">
              <button class="btn" @click="addRow" :disabled="store.loading">Add</button>
              <button class="btn btn-ghost" @click="showAddRow = false">Cancel</button>
            </div>
          </div>
        </div>
      </div>

      <FinancialStatementModal
        :open="statementOpen"
        :start="fsStart"
        :end="fsEnd"
        @close="statementOpen = false"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client.js'
import FinancialStatementModal from '../components/FinancialStatementModal.vue'
import { useLayout } from '../composables/useLayout.js'
import { useCoretaxStore } from '../stores/coretax.js'
import { useFinanceStore } from '../stores/finance.js'
import { NAV_SVGS } from '../utils/icons.js'

const store = useCoretaxStore()
const financeStore = useFinanceStore()
const { isDesktop } = useLayout()

const TAB_KEY = 'coretax_active_tab'
const tabs = [
  { id: 'import', label: 'Import', icon: NAV_SVGS.Audit },
  { id: 'review', label: 'Review', icon: NAV_SVGS.Dashboard },
  { id: 'mapping', label: 'Mapping', icon: NAV_SVGS.CoreTax },
  { id: 'reconcile', label: 'Reconcile', icon: NAV_SVGS.Audit },
  { id: 'export', label: 'Export', icon: NAV_SVGS.CoreTax },
]

function readStoredTab() {
  try { return localStorage.getItem(TAB_KEY) || 'import' } catch { return 'import' }
}

const activeTab = ref(readStoredTab())
const statementOpen = ref(false)
const fileInput = ref(null)
const selectedFile = ref(null)
const importResult = ref(null)
const reconcileResult = ref(null)
const exportResult = ref(null)
const showAddRow = ref(false)
const fsStart = ref('')
const fsEnd = ref('')
const runDiffResult = ref(null)
const activeBucket = ref(null)
const lifecycleBucketNames = ['STALE', 'WEAK', 'UNUSED', 'ORPHANED']

const newRow = ref({
  kind: 'asset', kode_harta: '', keterangan: '', owner: '',
  acquisition_year: null, current_amount_idr: null,
})

const currentYear = new Date().getFullYear()
const yearOptions = computed(() => {
  const years = []
  for (let y = currentYear + 1; y >= 2025; y--) years.push(y)
  return years
})

const monthOptions = computed(() => financeStore.dashboardMonthOptions?.map(o => o.value) || [])

watch(activeTab, (val) => {
  try { localStorage.setItem(TAB_KEY, val) } catch {}
})

// ── Actions ──────────────────────────────────────────────────────────────

function switchYear(val) {
  store.setTaxYear(Number(val))
  loadData()
}

async function loadData() {
  await Promise.all([
    store.fetchSummary(),
    store.fetchRows(),
    store.fetchMappings(),
    store.fetchExports(),
    store.fetchUnmappedPwm(),
    store.fetchLifecycleMappings(),
  ])
  // Default FS range to full tax year
  if (!fsStart.value) fsStart.value = `${store.taxYear - 1}-01`
  if (!fsEnd.value) fsEnd.value = `${store.taxYear - 1}-12`
}

function onFileSelected(e) {
  selectedFile.value = e.target.files?.[0] || null
}

async function uploadFile() {
  if (!selectedFile.value) return
  importResult.value = null
  try {
    const result = await store.uploadPriorYear(selectedFile.value)
    importResult.value = result
    selectedFile.value = null
    if (fileInput.value) fileInput.value.value = ''
  } catch (e) {
    importResult.value = { error: e?.message || String(e) }
  }
}

async function toggleCarryOverride(row) {
  const newVal = row.user_override_carry_forward != null
    ? (row.user_override_carry_forward ? 0 : 1)
    : (row.rule_default_carry_forward ? 0 : 1)
  await store.overrideStagingRow(store.stagingBatchId, row.id, newVal)
}

async function commitImport() {
  try {
    await store.commitStaging()
    importResult.value = null
    activeTab.value = 'review'
  } catch (e) {
    // Error shown via store.error
  }
}

async function discardImport() {
  await store.deleteStagingBatch()
  importResult.value = null
}

async function toggleLock(row, field) {
  if (row.amount_locked) {
    await store.unlockRow(row.id, field)
  } else {
    await store.lockRow(row.id, field, 'user toggle')
  }
}

async function editCell(row, field) {
  const current = row[field]
  const input = prompt(`Edit ${field} (IDR):`, current ?? '')
  if (input === null) return
  const val = input === '' ? null : Number(input)
  if (input !== '' && isNaN(val)) return
  await store.updateRow(row.id, { [field]: val })
}

async function resetRules() {
  await store.resetFromRules()
}

async function runReconcile() {
  reconcileResult.value = null
  try {
    reconcileResult.value = await store.runReconcile(
      { start_month: fsStart.value, end_month: fsEnd.value },
    )
  } catch (e) {
    reconcileResult.value = { error: e?.message || String(e) }
  }
}

// ── Mapping panel actions ────────────────────────────────────────────────

async function mapToRow(um) {
  // Open a prompt to select target stable_key
  const targetKey = prompt('Enter target stable_key (paste from Review tab):', '')
  if (!targetKey) return
  try {
    await store.assignMappingDirect({
      match_kind: um.match_kind,
      match_value: um.match_value,
      target_kode_harta: um.payload?.kode_harta || '',
      target_kind: um.source_kind === 'liability' ? 'liability' : 'asset',
      target_stable_key: targetKey,
      source: 'manual',
      confidence_score: 1.0,
      fingerprint_raw: um.fingerprint_raw,
    })
  } catch (e) {
    console.warn('Failed to assign mapping:', e)
  }
}

async function createRowFromUnmatched(um) {
  const payload = um.payload || {}
  const kind = um.source_kind === 'liability' ? 'liability' : 'asset'
  const kodeHarta = inferKodeHarta({ source_kind: um.source_kind, payload })
  const newRow = await store.createRow({
    kind,
    kode_harta: kodeHarta,
    keterangan: payload.institution || payload.asset_name || payload.liability_name || '',
    owner: payload.owner || '',
    institution: payload.institution || '',
    current_amount_idr: amountFromUnmatched(payload),
  })
  // Auto-create mapping
  if (newRow && newRow.stable_key) {
    try {
      await store.assignMappingDirect({
        match_kind: um.match_kind,
        match_value: um.match_value,
        target_kode_harta: kodeHarta,
        target_kind: kind,
        target_stable_key: newRow.stable_key,
        source: 'manual',
        confidence_score: 1.0,
        fingerprint_raw: um.fingerprint_raw,
      })
    } catch (e) {
      console.warn('Failed to create mapping:', e)
    }
  }
  await store.fetchUnmappedPwm()
}

async function suggestOne(um) {
  const suggestions = await store.suggestMappings([um])
  if (suggestions.length === 0) {
    alert('No suggestions found for this item.')
    return
  }
  const best = suggestions[0]
  const accept = confirm(`Suggestion: ${best.rule} (score ${best.confidence_score})\n→ ${best.target_stable_key}\n\nAccept?`)
  if (accept) {
    await store.assignMappingDirect({
      match_kind: um.match_kind,
      match_value: um.match_value,
      target_kode_harta: best.target_kode_harta || '',
      target_kind: um.source_kind === 'liability' ? 'liability' : 'asset',
      target_stable_key: best.target_stable_key,
      source: 'suggested',
      confidence_score: best.confidence_score,
      fingerprint_raw: um.fingerprint_raw,
    })
    await store.fetchUnmappedPwm()
  }
}

async function suggestAll() {
  const suggestions = await store.suggestMappings()
  if (suggestions.length === 0) {
    alert('No suggestions found.')
    return
  }
  // Filter to high-confidence (>= 0.9)
  const highConf = suggestions.filter(s => s.confidence_score >= 0.9)
  if (highConf.length === 0) {
    alert(`Found ${suggestions.length} suggestions, but none with confidence >= 0.9.`)
    return
  }
  const accept = confirm(`Accept ${highConf.length} high-confidence suggestions (>= 0.9)?\n\n${highConf.map(s => `${s.rule}: ${s.target_stable_key}`).join('\n')}`)
  if (accept) {
    for (const s of highConf) {
      try {
        await store.assignMappingDirect({
          match_kind: s.match_kind,
          match_value: s.match_value,
          target_kode_harta: s.target_kode_harta || '',
          target_kind: s.source_kind === 'liability' ? 'liability' : 'asset',
          target_stable_key: s.target_stable_key,
          source: 'suggested',
          confidence_score: s.confidence_score,
          fingerprint_raw: s.fingerprint_raw,
        })
      } catch (e) {
        console.warn('Failed to accept suggestion:', e)
      }
    }
    await store.fetchUnmappedPwm()
  }
}

async function confirmMappingAction(m) {
  await store.confirmMapping(m.id)
}

async function doFindRenames() {
  const candidates = await store.findRenames()
  if (candidates.length === 0) {
    alert('No rename candidates found.')
    return
  }
  alert(`Found ${candidates.length} rename candidates. Review in the table.`)
}

async function compareRuns() {
  const runs = store.reconcileRuns
  if (runs.length < 2) return
  const currentRunId = runs[0].id
  const prevRunId = runs[1].id
  runDiffResult.value = await store.fetchRunDiff(currentRunId, prevRunId)
}

async function deleteMapping(id) {
  await store.removeMapping(id)
}

async function createFromUnmatched(um) {
  const payload = um.payload || {}
  const kind = um.source_kind === 'liability' ? 'liability' : 'asset'
  const kodeHarta = inferKodeHarta(um)
  const newRow = await store.createRow({
    kind,
    kode_harta: kodeHarta,
    keterangan: payload.institution || payload.asset_name || payload.liability_name || '',
    owner: payload.owner || '',
    institution: payload.institution || '',
    current_amount_idr: amountFromUnmatched(payload),
  })
  // Persist a learned mapping so future reconciles auto-apply this row.
  if (newRow && newRow.stable_key && kodeHarta && payload.proposed_match_kind && payload.proposed_match_value) {
    try {
      await store.createMapping({
        match_kind: payload.proposed_match_kind,
        match_value: payload.proposed_match_value,
        target_kode_harta: kodeHarta,
        target_kind: kind,
        target_stable_key: newRow.stable_key,
      })
    } catch (e) {
      console.warn('Failed to persist learned mapping:', e)
    }
  }
  await store.fetchUnmatched()
}

function inferKodeHarta(um) {
  const payload = um.payload || {}
  if (um.source_kind === 'account_balance') return '012'
  if (um.source_kind === 'liability') return 'liability'
  if (um.source_kind === 'holding') {
    if (payload.asset_class === 'bond') return '034'
    if (payload.asset_class === 'mutual_fund') return '036'
    if (payload.asset_class === 'stock') return '039'
  }
  return ''
}

function amountFromUnmatched(payload) {
  if (payload.balance_idr != null) return payload.balance_idr
  if (payload.value != null) return payload.value
  if (payload.cost_basis_idr != null) return payload.cost_basis_idr
  if (payload.market_value_idr != null) return payload.market_value_idr
  return 0
}

async function addRow() {
  await store.createRow(newRow.value)
  showAddRow.value = false
  newRow.value = { kind: 'asset', kode_harta: '', keterangan: '', owner: '', acquisition_year: null, current_amount_idr: null }
}

async function doExport() {
  exportResult.value = null
  try {
    exportResult.value = await store.runExport()
  } catch (e) {
    exportResult.value = { error: e?.message || String(e) }
  }
}

function downloadUrl(fileId) {
  return api.coretaxExportDownload(fileId)
}

async function viewAudit(fileId) {
  try {
    const audit = await api.coretaxExportAudit(fileId)
    const blob = new Blob([JSON.stringify(audit, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = fileId.replace('.xlsx', '.audit.json')
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    alert('Failed to load audit: ' + (e?.message || e))
  }
}

// ── Formatters ───────────────────────────────────────────────────────────

function fmtIdr(val) {
  if (val == null) return '—'
  return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(val)
}

function fmtNum(val) {
  if (val == null) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(val)
}

const priorTotal = computed(() =>
  store.staging.reduce((sum, r) => sum + (r.parsed_carry_amount_idr || 0), 0)
)

const matchedTraces = computed(() =>
  (store.lastReconcileTrace || []).filter(t => t.status === 'filled' || t.status === 'locked_skipped')
)

const lowConfidenceCount = computed(() =>
  (store.lastReconcileTrace || []).filter(t => t.confidence_level === 'LOW').length
)

const staleCount = computed(() => {
  const b = store.lifecycleBuckets
  return (b.STALE || 0) + (b.WEAK || 0) + (b.UNUSED || 0) + (b.ORPHANED || 0)
})

const lifecycleItems = computed(() => {
  if (!activeBucket.value) return []
  return store.staleMappings.filter(m => m.lifecycle_bucket === activeBucket.value)
})

function fmtMonth(key) {
  if (!key) return ''
  const [y, m] = key.split('-')
  const d = new Date(Number(y), Number(m) - 1, 1)
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

onMounted(loadData)
</script>

<style scoped>
/* ── Two-column desktop shell (matches Settings.vue) ────────────────────── */
.settings-page {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
}

.settings-page--desktop {
  display: grid;
  grid-template-columns: 240px 1fr;
  align-items: start;
  min-height: 100%;
}

.settings-content { min-width: 0; }

/* ── Left nav — pixel-match Settings.vue sidebar ─────────────────────────── */
.settings-sub-nav {
  width: 240px;
  position: sticky;
  top: 0;
  padding: 16px 10px 20px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-right: 1px solid rgba(136,189,242,0.16);
  min-height: calc(100vh - 48px);
  margin-right: 24px;
}

.settings-sub-nav__title {
  font-size: 15px;
  font-weight: 800;
  color: var(--text);
  letter-spacing: -0.01em;
  padding: 4px 12px 12px;
}

.settings-sub-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  border: none;
  background: transparent;
  color: rgba(255,255,255,0.70);
  font-size: 14px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  transition: all 0.12s ease;
  width: 100%;
}
.settings-sub-nav__item:hover {
  background: rgba(136,189,242,0.12);
  color: #fff;
}
.settings-sub-nav__item.is-active {
  background: linear-gradient(180deg, rgba(136,189,242,0.22), rgba(106,137,167,0.15));
  color: #fff;
  box-shadow: inset 0 0 0 1px rgba(189,221,252,0.22);
}

.settings-sub-nav__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--primary-deep);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0.75;
  transition: opacity 0.12s;
}
.settings-sub-nav__icon :deep(svg) { width: 16px; height: 16px; }
.settings-sub-nav__item:hover .settings-sub-nav__icon,
.settings-sub-nav__item.is-active .settings-sub-nav__icon {
  opacity: 1;
  color: var(--primary);
}

/* ── Shared icon sizes (match Settings.vue scoped rules) ─────────────────── */
.settings-head-icon {
  width: 16px;
  height: 16px;
  margin-right: 8px;
  vertical-align: middle;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--primary-deep);
  flex-shrink: 0;
}
.settings-head-icon :deep(svg) { width: 16px; height: 16px; }

.setting-title-icon {
  width: 15px;
  height: 15px;
  margin-right: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--primary-deep);
  flex-shrink: 0;
}
.setting-title-icon :deep(svg) { width: 15px; height: 15px; }

/* ── CoreTax-specific component styles ──────────────────────────────────── */
.settings-grid { display: grid; gap: 16px; }

/* Force single card per row in right panel */
.settings-page--desktop .settings-grid {
  grid-template-columns: 1fr !important;
}

.setting-row { margin-top: 12px; }
.setting-row-range, .action-row { display: flex; gap: 12px; flex-wrap: wrap; }
.range-field { min-width: 140px; flex: 0 1 200px; }
.reconcile-range-row {
  align-items: flex-end;
  justify-content: flex-start;
  gap: 16px;
  max-width: 440px;
}
.reconcile-range-row .range-field {
  flex: 0 0 180px;
  min-width: 0;
}

.year-select { width: auto; min-width: 90px; margin-left: 8px; }
.coverage-chip { font-size: 13px; font-weight: 600; margin-left: 12px; padding: 3px 10px; border-radius: 8px; background: rgba(255,99,99,0.15); color: #ffd2d2; }
.coverage-chip.ok { background: rgba(92,199,129,0.15); color: #d4ffe2; }
.file-input { color: rgba(255,255,255,0.7); }

.btn-icon { background: none; border: none; cursor: pointer; font-size: 16px; padding: 4px; }

.table-wrap { overflow-x: auto; margin-top: 8px; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th { text-align: left; padding: 8px 10px; color: var(--text-muted); font-weight: 700; border-bottom: 1px solid var(--border, rgba(255,255,255,0.08)); white-space: nowrap; }
.data-table td { padding: 6px 10px; border-bottom: 1px solid var(--border-subtle, rgba(255,255,255,0.04)); }
.cell-num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cell-desc { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cell-src { font-size: 11px; color: var(--text-muted); }
.th-prior { text-align: right; width: 140px; }
.row-total td { font-weight: 700; border-top: 2px solid var(--border, rgba(255,255,255,0.12)); padding-top: 10px; }
.row-locked { opacity: 0.7; }
.lock-badge { font-size: 11px; }
.editable { cursor: pointer; }
.editable:hover { background: rgba(136,189,242,0.08); }

.preview-summary { display: grid; gap: 10px; }
.preview-summary__line { border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,0.03); padding: 10px 12px; }
.preview-summary__line.ok { color: #c8ffd8; }
.preview-summary__line.warn { color: #fff3cd; }

.unmatched-list, .run-history { margin: 8px 0 0 18px; color: var(--text-muted); font-size: 13px; }
.unmatched-cards { display: grid; gap: 8px; margin-top: 8px; }
.unmatched-card { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: rgba(255,255,255,0.03); font-size: 13px; }

/* ── Mapping-first reconciliation UI ─────────────────────────────────── */
.section-summary {
  cursor: pointer; font-weight: 700; font-size: 14px; color: var(--text);
  display: flex; align-items: center; gap: 8px;
}
.badge {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px;
  background: rgba(136,189,242,0.2); color: rgba(136,189,242,0.9);
  font-size: 11px; font-weight: 700;
}
.badge-warn { background: rgba(255,193,7,0.2); color: #ffc107; }
.conf-dot {
  display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 8px;
  font-size: 11px; font-weight: 700;
}
.conf-high { background: rgba(92,199,129,0.15); color: #c8ffd8; }
.conf-medium { background: rgba(255,193,7,0.15); color: #fff3cd; }
.conf-low { background: rgba(255,99,99,0.15); color: #ffd2d2; }
.lifecycle-pills { display: flex; gap: 6px; flex-wrap: wrap; }
.pill {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border);
  background: transparent; color: var(--text-muted); font-size: 12px; font-weight: 600;
  cursor: pointer; transition: all 0.12s;
}
.pill:hover { background: rgba(136,189,242,0.1); }
.pill.is-active { background: rgba(136,189,242,0.2); color: #fff; border-color: rgba(136,189,242,0.4); }
.pill-count { font-size: 10px; opacity: 0.7; }

.link { color: rgba(136,189,242,0.9); text-decoration: none; }
.link:hover { text-decoration: underline; }

.modal-overlay { position: fixed; inset: 0; z-index: 100; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; }
.modal-card { background: #141820; border: 1px solid rgba(255,255,255,0.12); border-radius: 16px; padding: 24px; min-width: 340px; max-width: 480px; }
.form-grid { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: center; margin-top: 12px; }
.form-grid .range-label { margin-bottom: 0; }
</style>
