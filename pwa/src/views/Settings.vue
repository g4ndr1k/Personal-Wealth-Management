<template>
  <div>
    <div class="section-hd">⚙️ Settings</div>
    <div class="settings-grid">

    <div class="setting-card">
      <div class="setting-title">📊 Dashboard Range</div>
      <div class="setting-desc">
        Choose which months appear on the main dashboard. Months before Jan 2026 are always hidden.
      </div>
      <div class="setting-row setting-row-range">
        <div class="range-field">
          <label class="range-label">Start Month</label>
          <select
            class="range-select"
            :value="store.dashboardStartMonth"
            @change="store.setDashboardRange($event.target.value, store.dashboardEndMonth)"
          >
            <option
              v-for="option in store.dashboardMonthOptions.filter(option => option.value <= store.dashboardEndMonth)"
              :key="`start-${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
        <div class="range-field">
          <label class="range-label">End Month</label>
          <select
            class="range-select"
            :value="store.dashboardEndMonth"
            @change="store.setDashboardRange(store.dashboardStartMonth, $event.target.value)"
          >
            <option
              v-for="option in store.dashboardMonthOptions.filter(option => option.value >= store.dashboardStartMonth)"
              :key="`end-${option.value}`"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </div>
      </div>
      <div class="setting-desc" style="margin-top:10px">
        Active range: <strong>{{ store.dashboardRangeLabel }}</strong>
      </div>
    </div>

    <!-- Health status -->
    <div class="setting-card">
      <div class="setting-title">📡 API Status</div>
      <div class="setting-desc">Live status from the FastAPI backend.</div>
      <div v-if="!store.health" class="loading" style="padding:10px 0"><div class="spinner"></div> Checking…</div>
      <div v-else>
        <div :class="['alert', store.health.status === 'ok' ? 'alert-success' : 'alert-error']" style="margin-bottom:12px">
          {{ store.health.status === 'ok' ? '✅ Connected' : '❌ Offline' }}
        </div>
        <div class="status-grid">
          <div class="status-item">
            <div class="sk">Transactions</div>
            <div class="sv">{{ store.health.transaction_count?.toLocaleString() ?? '—' }}</div>
          </div>
          <div class="status-item">
            <div class="sk">Needs Review</div>
            <div class="sv" :class="store.health.needs_review > 0 ? 'text-expense' : 'text-income'">
              {{ store.health.needs_review ?? '—' }}
            </div>
          </div>
          <div class="status-item" style="grid-column:1/-1">
            <div class="sk">Last Sync</div>
            <div class="sv" style="font-size:13px">{{ store.health.last_sync || 'Never' }}</div>
          </div>
        </div>
        <button class="btn btn-ghost btn-sm" style="margin-top:12px" @click="store.loadHealth({ forceFresh: true })">
          🔄 Refresh status
        </button>
      </div>
    </div>

    <div class="setting-card">
      <div class="setting-title">📱 Mobile Data Cache</div>
      <div class="setting-desc">
        iPhone PWA reads cached API data for up to 24 hours. Use this to pull fresh data immediately instead of waiting for the daily refresh window.
      </div>
      <button
        class="btn btn-primary btn-block"
        :disabled="refreshCacheState.loading"
        @click="refreshMobileCache"
      >
        <span v-if="refreshCacheState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Refreshing cache…</span>
        <span v-else>🔄 Refresh Mobile Data Now</span>
      </button>
      <div v-if="refreshCacheState.error" class="alert alert-error" style="margin-top:10px">
        ❌ {{ refreshCacheState.error }}
      </div>
      <div v-else-if="refreshCacheState.doneAt" class="result-box">
        <div class="result-row">
          <span class="rk">Last manual refresh</span>
          <span class="rv">{{ refreshCacheState.doneAt }}</span>
        </div>
      </div>
    </div>

    <!-- Sync from Google Sheets -->
    <div class="setting-card">
      <div class="setting-title">☁️ Sync from Google Sheets</div>
      <div class="setting-desc">
        Pull the latest data from your Google Sheets spreadsheet into the local SQLite cache.
        This replaces all rows atomically — no partial states.
      </div>
      <button
        class="btn btn-primary btn-block"
        :disabled="syncState.loading"
        @click="doSync"
      >
        <span v-if="syncState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Syncing…</span>
        <span v-else>🔄 Sync Now</span>
      </button>

      <!-- Sync result -->
      <div v-if="syncState.error" class="alert alert-error" style="margin-top:10px">
        ❌ {{ syncState.error }}
      </div>
      <div v-else-if="syncState.result" class="result-box">
        <div class="result-row">
          <span class="rk">Synced at</span>
          <span class="rv">{{ syncState.result.synced_at }}</span>
        </div>
        <div class="result-row">
          <span class="rk">Transactions</span>
          <span class="rv">{{ syncState.result.transactions_count?.toLocaleString() }}</span>
        </div>
        <div class="result-row">
          <span class="rk">Aliases</span>
          <span class="rv">{{ syncState.result.aliases_count }}</span>
        </div>
        <div class="result-row">
          <span class="rk">Categories</span>
          <span class="rv">{{ syncState.result.categories_count }}</span>
        </div>
        <div class="result-row">
          <span class="rk">Duration</span>
          <span class="rv">{{ syncState.result.duration_s }}s</span>
        </div>
      </div>
    </div>

    <!-- Import from XLSX -->
    <div class="setting-card">
      <div class="setting-title">📥 Import from XLSX</div>
      <div class="setting-desc">
        Run the Stage 1 importer to process
        <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">ALL_TRANSACTIONS.xlsx</code>
        and push new rows to Google Sheets.
        After a successful import, a Sheets → SQLite sync runs automatically.
      </div>

      <div class="setting-row">
        <label>
          <input type="checkbox" v-model="importOpts.dry_run" />
          Dry run (preview only, no writes)
        </label>
      </div>
      <div class="setting-row">
        <label>
          <input type="checkbox" v-model="importOpts.overwrite" />
          Overwrite existing rows (re-import duplicates)
        </label>
      </div>

      <button
        class="btn btn-primary btn-block"
        style="margin-top:4px"
        :disabled="importState.loading"
        @click="doImport"
      >
        <span v-if="importState.loading"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Importing…</span>
        <span v-else>📥 {{ importOpts.dry_run ? 'Dry Run' : 'Import' }}</span>
      </button>

      <!-- Import result -->
      <div v-if="importState.error" class="alert alert-error" style="margin-top:10px">
        ❌ {{ importState.error }}
      </div>
      <div v-else-if="importState.result" class="result-box">
        <div class="result-row">
          <span class="rk">Rows added</span>
          <span class="rv" :class="(importState.result.rows_added || 0) > 0 ? 'text-income' : 'text-neutral'">
            {{ importState.result.rows_added ?? 0 }}
          </span>
        </div>
        <template v-if="importState.result.sync_stats">
          <div class="result-row">
            <span class="rk">After-sync transactions</span>
            <span class="rv">{{ importState.result.sync_stats.transactions_count?.toLocaleString() }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Sync duration</span>
            <span class="rv">{{ importState.result.sync_stats.duration_s }}s</span>
          </div>
        </template>
        <div v-if="importOpts.dry_run" class="result-row">
          <span class="rk">Mode</span>
          <span class="rv" style="color:var(--warning)">Dry run — no changes written</span>
        </div>
      </div>
    </div>

    <div class="setting-card">
      <div class="setting-title">🧩 PDF Pipeline</div>
      <div class="setting-desc">
        Run the end-to-end pipeline from <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_inbox</code>
        through import and sync. Desktop only, and controlled by the bridge pipeline setting.
      </div>

      <div class="pipeline-grid">
        <button
          class="btn btn-primary"
          :disabled="pipelineState.loading || pipelineState.status?.status === 'running'"
          @click="runPipeline"
        >
          <span v-if="pipelineState.loading || pipelineState.status?.status === 'running'">
            <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
            Running pipeline…
          </span>
          <span v-else>🔄 Run Pipeline</span>
        </button>

        <div class="result-box" v-if="pipelineState.status">
          <div class="result-row">
            <span class="rk">Status</span>
            <span class="rv">{{ pipelineState.status.status || 'idle' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Last run</span>
            <span class="rv">{{ pipelineState.status.last_run_at || 'Never' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Next run</span>
            <span class="rv">{{ pipelineState.status.next_scheduled_at || 'Not scheduled' }}</span>
          </div>
          <div class="result-row">
            <span class="rk">Last result</span>
            <span class="rv">
              {{ formatPipelineSummary(pipelineState.status.last_result) }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="pipelineState.error" class="alert alert-error" style="margin-top:10px">
        ❌ {{ pipelineState.error }}
      </div>
    </div>

    <!-- ── Process Local PDFs ──────────────────────────────────────────────── -->
    <div class="setting-card">
      <div class="setting-title">📄 Process Local PDFs</div>
      <div class="setting-desc">
        Scan <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_inbox</code>
        and <code style="font-size:11px;background:var(--bg);padding:2px 5px;border-radius:3px">data/pdf_unlocked</code>
        for bank statement PDFs. Review the list, see when each file was last processed,
        and run only the PDFs you select.
      </div>

      <!-- Non-Mac notice -->
      <div v-if="!isDesktopMac" class="pdf-unavail-note">
        Only available on macOS desktop. Open this app on your Mac controller.
      </div>

      <div class="pdf-desktop-tools">
        <div class="pdf-desktop-actions">
          <span
            class="pdf-btn-wrapper"
            :title="!isDesktopMac ? 'This feature is only available on the Desktop controller.' : ''"
          >
            <button
              class="btn btn-ghost btn-sm"
              :disabled="!isDesktopMac"
              :class="{ 'btn-disabled-look': !isDesktopMac }"
              @click="togglePdfWorkspace"
            >
              {{ showPdfWorkspace ? 'Hide PDF Workspace' : 'Open PDF Workspace' }}
            </button>
          </span>
        </div>

        <div v-if="showPdfWorkspace" class="pdf-workspace">
          <div class="pdf-workspace-toolbar">
            <button
              class="btn btn-ghost btn-sm"
              :disabled="pdfWorkspace.loading || pdf.phase === 'processing'"
              @click="loadPdfWorkspace"
            >
              {{ pdfWorkspace.loading ? 'Refreshing…' : 'Refresh' }}
            </button>
            <input
              v-model.trim="pdfWorkspace.search"
              class="pdf-search"
              type="search"
              placeholder="Search filename…"
              :disabled="pdfWorkspace.loading"
            />
            <select
              v-model="pdfWorkspace.folder"
              class="pdf-filter"
              :disabled="pdfWorkspace.loading"
            >
              <option value="all">All folders</option>
              <option value="pdf_inbox">pdf_inbox</option>
              <option value="pdf_unlocked">pdf_unlocked</option>
            </select>
          </div>

          <div v-if="pdf.fatalError" class="alert alert-error" style="margin-top:10px">
            ❌ {{ pdf.fatalError }}
          </div>
          <div v-else-if="pdfWorkspace.error" class="alert alert-error" style="margin-top:10px">
            ❌ {{ pdfWorkspace.error }}
          </div>

          <div v-if="pdf.phase === 'processing' && pdf.total > 0" style="margin-top:12px">
            <div class="pdf-summary-bar">
              <span class="pdf-badge pdf-badge-ok">✅ {{ pdfCounts.ok }}</span>
              <span v-if="pdfCounts.skipped > 0" class="pdf-badge pdf-badge-skip">⏭ {{ pdfCounts.skipped }} skipped</span>
              <span v-if="pdfCounts.error > 0" class="pdf-badge pdf-badge-err">❌ {{ pdfCounts.error }} failed</span>
              <span class="pdf-badge pdf-badge-pend">⏳ {{ pdf.processed }} / {{ pdf.total }}</span>
            </div>
            <div class="pdf-progress-bar-wrap">
              <div
                class="pdf-progress-bar"
                :style="{ width: Math.round(100 * pdf.processed / pdf.total) + '%' }"
              ></div>
            </div>
            <div v-if="pdf.current" class="pdf-current-file">↳ {{ pdf.current }}</div>
          </div>

          <div v-if="pdfWorkspace.loading" class="pdf-empty-state">
            <span class="spinner" style="width:16px;height:16px;border-width:2px"></span>
            Loading local PDFs…
          </div>

          <template v-else>
            <div v-if="visiblePdfFiles.length === 0" class="pdf-empty-state">
              {{ pdfWorkspace.files.length === 0 ? 'No PDF files found in pdf_inbox or pdf_unlocked.' : 'No PDFs match the current search or folder filter.' }}
            </div>

            <div v-else class="pdf-groups">
              <div class="pdf-groups-toolbar">
                <label class="pdf-master-toggle">
                  <input
                    type="checkbox"
                    :checked="allVisibleSelected"
                    :disabled="pdf.phase === 'processing'"
                    @change="toggleVisibleSelection($event.target.checked)"
                  />
                  <span>Select all matching PDFs</span>
                </label>
              </div>

              <section
                v-for="institution in groupedPdfFiles"
                :key="institution.key"
                class="pdf-group-card"
              >
                <button
                  class="pdf-group-header"
                  type="button"
                  @click="toggleInstitutionGroup(institution.key)"
                >
                  <div class="pdf-group-title">
                    <span class="pdf-group-chevron">{{ isInstitutionExpanded(institution.key) ? '▾' : '▸' }}</span>
                    <span>{{ institution.label }}</span>
                  </div>
                  <div class="pdf-group-meta">
                    <span>{{ institution.fileCount }} PDFs</span>
                    <span>{{ institution.months.length }} months</span>
                  </div>
                </button>

                <div v-if="isInstitutionExpanded(institution.key)" class="pdf-month-list">
                  <section
                    v-for="month in institution.months"
                    :key="month.key"
                    class="pdf-month-card"
                  >
                    <button
                      class="pdf-month-header"
                      type="button"
                      @click="toggleMonthGroup(month.key)"
                    >
                      <div class="pdf-group-title">
                        <span class="pdf-group-chevron">{{ isMonthExpanded(month.key) ? '▾' : '▸' }}</span>
                        <span>{{ month.label }}</span>
                      </div>
                      <div class="pdf-group-meta">
                        <span>{{ month.files.length }} PDFs</span>
                      </div>
                    </button>

                    <div v-if="isMonthExpanded(month.key)" class="pdf-table-wrap">
                      <table class="pdf-table">
                        <thead>
                          <tr>
                            <th class="pdf-checkbox-col"></th>
                            <th>PDF File</th>
                            <th>Folder</th>
                            <th>Last Processed</th>
                            <th>Status</th>
                            <th>Details</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="file in month.files" :key="file.key">
                            <td class="pdf-checkbox-col">
                              <input
                                v-model="file.selected"
                                type="checkbox"
                                :disabled="pdf.phase === 'processing'"
                              />
                            </td>
                            <td>
                              <div class="pdf-name-cell">
                                <div class="pdf-name-main">{{ file.filename }}</div>
                                <div class="pdf-name-sub">
                                  <span v-if="file.relativeDir">{{ file.relativeDir }} · </span>
                                  {{ formatPdfSize(file.sizeKb) }} · Modified {{ formatPdfDate(file.mtime) }}
                                </div>
                              </div>
                            </td>
                            <td>
                              <span class="pdf-folder-pill">{{ file.folder }}</span>
                            </td>
                            <td>{{ formatPdfDate(file.lastProcessedAt, true) }}</td>
                            <td>
                              <span :class="['pdf-status-chip', `pdf-status-${getPdfStatusClass(file)}`]">
                                {{ getPdfStatusLabel(file) }}
                              </span>
                            </td>
                            <td class="pdf-detail-cell">{{ getPdfDetail(file) }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </section>
                </div>
              </section>
            </div>
          </template>

          <div class="pdf-workspace-footer">
            <div class="pdf-selection-note">
              {{ selectedPdfCount }} selected · {{ visiblePdfFiles.length }} shown · {{ pdfWorkspace.files.length }} total
            </div>
            <div class="pdf-workspace-footer-actions">
              <button
                class="btn btn-ghost btn-sm"
                :disabled="selectedPdfCount === 0 || pdf.phase === 'processing'"
                @click="clearPdfSelection"
              >
                Clear Selection
              </button>
              <button
                class="btn btn-primary btn-sm"
                :disabled="selectedPdfCount === 0 || pdf.phase === 'processing' || pdfWorkspace.loading"
                @click="processSelectedPdfs"
              >
                <span v-if="pdf.phase === 'processing'">
                  <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
                  Processing {{ pdf.processed }} / {{ pdf.total }}…
                </span>
                <span v-else>
                  Process Selected
                </span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- About -->
    <div class="setting-card">
      <div class="setting-title">ℹ️ About</div>
      <div style="font-size:12px;color:var(--text-muted);line-height:1.7">
        <div><strong>Finance Dashboard</strong> — Stage 2-B</div>
        <div>Vue 3 PWA · FastAPI backend · SQLite read cache · Google Sheets source of truth</div>
        <div style="margin-top:6px">
          API: <code style="font-size:11px">localhost:8090</code>
        </div>
      </div>
    </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()

const syncState   = ref({ loading: false, result: null, error: null })
const importState = ref({ loading: false, result: null, error: null })
const importOpts  = ref({ dry_run: false, overwrite: false })
const refreshCacheState = ref({ loading: false, error: null, doneAt: '' })
const pipelineState = ref({ loading: false, status: null, error: null })
const showPdfWorkspace = ref(false)

// ── Mac desktop detection ────────────────────────────────────────────────────
// navigator.platform is "MacIntel" on macOS (and iPadOS ≥13 — exclude via maxTouchPoints).
const isDesktopMac = computed(() => {
  const platform     = navigator.platform || ''
  const ua           = navigator.userAgent || ''
  const looksLikeMac = platform.startsWith('Mac') || ua.includes('Macintosh')
  const isTouch      = navigator.maxTouchPoints > 1
  return looksLikeMac && !isTouch
})

// ── PDF processing state ─────────────────────────────────────────────────────
const EMPTY_PDF_STATE = () => ({
  phase: 'idle',   // 'idle' | 'processing'
  current: '',
  processed: 0,
  total: 0,
  fatalError: null,
})
const pdf = ref(EMPTY_PDF_STATE())
const pdfWorkspace = ref({
  loading: false,
  error: null,
  loaded: false,
  search: '',
  folder: 'all',
  files: [],
})
const pdfExpanded = ref({
  institutions: {},
  months: {},
})

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const visiblePdfFiles = computed(() => {
  const q = pdfWorkspace.value.search.trim().toLowerCase()
  return pdfWorkspace.value.files
    .filter((file) => {
      const matchesFolder = pdfWorkspace.value.folder === 'all' || file.folder === pdfWorkspace.value.folder
      const haystack = `${file.filename} ${file.relativePath} ${file.institutionLabel}`.toLowerCase()
      const matchesSearch = !q || haystack.includes(q)
      return matchesFolder && matchesSearch
    })
    .slice()
    .sort((a, b) => {
      const byPath = a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true, sensitivity: 'base' })
      if (byPath !== 0) return byPath
      return a.folder.localeCompare(b.folder, undefined, { sensitivity: 'base' })
    })
})

const selectedPdfCount = computed(() =>
  pdfWorkspace.value.files.filter(file => file.selected).length
)

const allVisibleSelected = computed(() =>
  visiblePdfFiles.value.length > 0 && visiblePdfFiles.value.every(file => file.selected)
)

const groupedPdfFiles = computed(() => {
  const groups = new Map()

  for (const file of visiblePdfFiles.value) {
    const institutionKey = file.institutionKey
    if (!groups.has(institutionKey)) {
      groups.set(institutionKey, {
        key: institutionKey,
        label: file.institutionLabel,
        months: new Map(),
        fileCount: 0,
      })
    }

    const institution = groups.get(institutionKey)
    institution.fileCount += 1

    if (!institution.months.has(file.monthKey)) {
      institution.months.set(file.monthKey, {
        key: file.monthKey,
        label: file.monthLabel,
        sortKey: file.monthSortKey,
        files: [],
      })
    }

    institution.months.get(file.monthKey).files.push(file)
  }

  return Array.from(groups.values())
    .map((institution) => ({
      ...institution,
      months: Array.from(institution.months.values())
        .sort((a, b) => b.sortKey.localeCompare(a.sortKey))
        .map((month) => ({
          ...month,
          files: month.files.slice().sort((a, b) =>
            a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true, sensitivity: 'base' })
          ),
        })),
    }))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }))
})

const pdfCounts = computed(() => {
  const counts = { ok: 0, skipped: 0, error: 0 }
  for (const file of pdfWorkspace.value.files) {
    const status = getPdfStatusClass(file)
    if (status === 'ok') counts.ok++
    else if (status === 'skipped') counts.skipped++
    else if (status === 'error') counts.error++
  }
  return counts
})

function resetPdf() {
  pdf.value = EMPTY_PDF_STATE()
}

// ── Poll /api/pdf/local-status until done (max 3 min) ───────────────────────
async function pollStatus(jobId, timeoutMs = 180_000) {
  const deadline = Date.now() + timeoutMs
  let transientErrors = 0
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2500))
    try {
      const s = await api.pdfLocalStatus(jobId)
      transientErrors = 0
      if (s.status === 'done' || s.status === 'error') return s
    } catch (err) {
      const message = String(err?.message || '')
      const isTransient = message.includes('502') || message.includes('503') || message.includes('504') || message.includes('Bridge unreachable')
      if (isTransient && transientErrors < 5) {
        transientErrors += 1
        continue
      }
      throw err
    }
  }
  throw new Error('Timed out after 3 min')
}

function formatPdfDate(value, includeTime = false) {
  if (!value) return 'Never'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'
  return date.toLocaleString([], includeTime
    ? { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { year: 'numeric', month: 'short', day: 'numeric' })
}

function formatPdfSize(sizeKb) {
  return `${Number(sizeKb || 0).toFixed(1)} KB`
}

function truncateText(text, max = 80) {
  return text && text.length > max ? `${text.slice(0, max - 1)}…` : (text || '')
}

function inferInstitution(filename) {
  const upper = filename.toUpperCase()
  if (upper.includes('BNI_SEKURITAS')) return { key: 'bni-sekuritas', label: 'BNI Sekuritas' }
  if (upper.startsWith('BCA') || upper.includes('BCA_')) return { key: 'bca', label: 'BCA' }
  if (upper.includes('CIMB')) return { key: 'cimb-niaga', label: 'CIMB Niaga' }
  if (upper.includes('MAYBANK')) return { key: 'maybank', label: 'Maybank' }
  if (upper.includes('PERMATA')) return { key: 'permata', label: 'Permata' }
  if (upper.includes('IPOT') || upper.includes('INDO PREMIER')) return { key: 'ipot', label: 'IPOT' }
  if (upper.includes('STOCKBIT')) return { key: 'stockbit', label: 'Stockbit' }
  if (upper.includes('BNI')) return { key: 'bni', label: 'BNI' }
  if (upper.includes('SEKURITAS')) return { key: 'sekuritas', label: 'Sekuritas' }
  return { key: 'other', label: 'Other' }
}

function inferMonthBucket(filename) {
  const name = filename.toUpperCase()
  const monthNameMap = {
    JAN: '01', FEB: '02', MAR: '03', APR: '04', MAY: '05', JUN: '06',
    JUL: '07', AUG: '08', SEP: '09', OCT: '10', NOV: '11', DEC: '12',
  }

  const candidates = [
    name.match(/(?:^|_)(\d{2})_(20\d{2})(?=\.|_|$)/),
    name.match(/(?:^|_)(20\d{2})_(\d{2})(?=\.|_|$)/),
    name.match(/(20\d{2})-(\d{2})-(\d{2})/),
    name.match(/(20\d{2})(\d{2})(\d{2})/),
    name.match(/(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(20\d{2})/),
  ].filter(Boolean)

  for (const match of candidates) {
    let year = ''
    let month = ''

    if (match[0].includes('-')) {
      year = match[1]
      month = match[2]
    } else if (monthNameMap[match[1]]) {
      month = monthNameMap[match[1]]
      year = match[2]
    } else if (match[1].length === 2 && match[2].length === 4) {
      month = match[1]
      year = match[2]
    } else {
      year = match[1]
      month = match[2]
    }

    if (/^20\d{2}$/.test(year) && Number(month) >= 1 && Number(month) <= 12) {
      return {
        key: `${year}-${month}`,
        label: `${MONTH_LABELS[Number(month) - 1]} ${year}`,
        sortKey: `${year}-${month}`,
      }
    }
  }

  return {
    key: 'unknown-period',
    label: 'Unknown Period',
    sortKey: '0000-00',
  }
}

function mapWorkspaceFile(file, previous) {
  const institution = inferInstitution(file.filename)
  const monthBucket = inferMonthBucket(file.filename)
  const relativePath = file.relative_path || file.filename
  const lastSlash = relativePath.lastIndexOf('/')
  const relativeDir = lastSlash >= 0 ? relativePath.slice(0, lastSlash) : ''
  return {
    key: `${file.folder}/${relativePath}`,
    folder: file.folder,
    filename: file.filename,
    relativePath,
    relativeDir,
    sizeKb: file.size_kb,
    mtime: file.mtime,
    lastProcessedAt: file.last_processed_at,
    lastStatus: file.last_status,
    lastError: file.last_error || '',
    selected: previous?.selected || false,
    processingState: previous?.processingState || null,
    processingMeta: previous?.processingMeta || '',
    institutionKey: institution.key,
    institutionLabel: institution.label,
    monthKey: `${institution.key}:${monthBucket.key}`,
    monthLabel: monthBucket.label,
    monthSortKey: monthBucket.sortKey,
  }
}

async function loadPdfWorkspace() {
  pdfWorkspace.value.loading = true
  pdfWorkspace.value.error = null
  pdf.value.fatalError = null
  try {
    const res = await api.pdfLocalWorkspace()
    const previousByKey = new Map(pdfWorkspace.value.files.map(file => [file.key, file]))
    pdfWorkspace.value.files = (res.files || []).map(file =>
      mapWorkspaceFile(file, previousByKey.get(`${file.folder}/${file.relative_path || file.filename}`))
    )
    pdfWorkspace.value.loaded = true
  } catch (err) {
    pdfWorkspace.value.error = err.message
  } finally {
    pdfWorkspace.value.loading = false
  }
}

async function togglePdfWorkspace() {
  showPdfWorkspace.value = !showPdfWorkspace.value
  if (showPdfWorkspace.value && !pdfWorkspace.value.loaded) {
    await loadPdfWorkspace()
  }
}

function clearPdfSelection() {
  pdfWorkspace.value.files.forEach(file => { file.selected = false })
}

function toggleVisibleSelection(checked) {
  visiblePdfFiles.value.forEach(file => { file.selected = checked })
}

function isInstitutionExpanded(key) {
  return pdfWorkspace.value.search.trim() !== '' || Boolean(pdfExpanded.value.institutions[key])
}

function isMonthExpanded(key) {
  return pdfWorkspace.value.search.trim() !== '' || Boolean(pdfExpanded.value.months[key])
}

function toggleInstitutionGroup(key) {
  pdfExpanded.value.institutions[key] = !pdfExpanded.value.institutions[key]
}

function toggleMonthGroup(key) {
  pdfExpanded.value.months[key] = !pdfExpanded.value.months[key]
}

function getPdfStatusClass(file) {
  if (file.processingState) return file.processingState
  if (file.lastStatus === 'done') return 'ok'
  if (file.lastStatus === 'error') return 'error'
  if (file.lastStatus === 'pending') return 'pending'
  return 'new'
}

function getPdfStatusLabel(file) {
  const status = getPdfStatusClass(file)
  return {
    new: 'New',
    pending: 'Pending',
    processing: 'Processing',
    ok: 'Done',
    skipped: 'Skipped',
    error: 'Failed',
  }[status] || 'New'
}

function getPdfDetail(file) {
  if (file.processingState === 'processing') return 'Processing now…'
  if (file.processingMeta) return file.processingMeta
  if (file.lastStatus === 'error' && file.lastError) return truncateText(file.lastError, 120)
  if (file.lastStatus === 'done') return 'Processed previously'
  return 'Ready to process'
}

function applyPdfRunResult(file, final) {
  file.lastProcessedAt = final.created_at || new Date().toISOString()
  const log = String(final.log || '').toLowerCase()
  if (final.status === 'error') {
    file.processingState = 'error'
    file.processingMeta = truncateText(final.error || 'Parser error')
    file.lastStatus = 'error'
    file.lastError = final.error || 'Parser error'
    return
  }

  file.lastStatus = 'done'
  file.lastError = ''

  if (log.includes('duplicate') || log.includes('skipped') || log.includes('already imported')) {
    file.processingState = 'skipped'
    file.processingMeta = 'Already imported'
    return
  }

  file.processingState = 'ok'
  const match = String(final.log || '').match(/(?:rows added|upserted|bond|fund)[^\n]*/i)
  file.processingMeta = match ? truncateText(match[0].trim(), 80) : 'Imported'
}

async function processSelectedPdfs() {
  const selected = pdfWorkspace.value.files.filter(file => file.selected)
  if (selected.length === 0) return

  resetPdf()
  pdf.value.phase = 'processing'
  pdf.value.total = selected.length

  for (let i = 0; i < selected.length; i++) {
    const file = selected[i]
    file.processingState = 'processing'
    file.processingMeta = ''
    pdf.value.current = file.filename

    try {
      const res = await api.processLocalPdf(file.folder, file.relativePath)
      const jobId = res.job_id
      if (!jobId) throw new Error('No job_id returned')
      const final = await pollStatus(jobId)
      applyPdfRunResult(file, final)
    } catch (err) {
      await loadPdfWorkspace()
      const refreshed = pdfWorkspace.value.files.find(candidate => candidate.key === file.key)
      if (refreshed && refreshed.lastStatus === 'done') {
        continue
      }
      file.processingState = 'error'
      file.processingMeta = truncateText(err.message)
      file.lastStatus = 'error'
      file.lastError = err.message
    }

    pdf.value.processed = i + 1
  }

  pdf.value.phase = 'idle'
  pdf.value.current = ''
  await loadPdfWorkspace()
}

// ── Existing actions ─────────────────────────────────────────────────────────
async function doSync() {
  syncState.value = { loading: true, result: null, error: null }
  try {
    const res = await api.sync()
    syncState.value.result = res.queued ? { status: 'queued' } : res
    if (!res.queued) {
      await store.loadHealth({ forceFresh: true })
      await store.loadCategories({ forceFresh: true })
    }
  } catch (e) {
    syncState.value.error = e.message
  } finally {
    syncState.value.loading = false
  }
}

async function doImport() {
  importState.value = { loading: true, result: null, error: null }
  try {
    const res = await api.importData({
      dry_run:   importOpts.value.dry_run,
      overwrite: importOpts.value.overwrite,
    })
    importState.value.result = res.queued ? { status: 'queued' } : res
    if (!res.queued && !importOpts.value.dry_run) await store.loadHealth({ forceFresh: true })
  } catch (e) {
    importState.value.error = e.message
  } finally {
    importState.value.loading = false
  }
}

function formatPipelineSummary(result) {
  if (!result) return 'No runs yet'
  return `${result.files_ok || 0} ok, ${result.files_failed || 0} failed, ${result.files_skipped || 0} skipped, ${result.import_new_tx || 0} imported`
}

async function refreshMobileCache() {
  const previousDoneAt = refreshCacheState.value.doneAt
  refreshCacheState.value = { loading: true, error: null, doneAt: previousDoneAt }
  try {
    await api.refreshReferenceData()
    await store.bootstrap({ forceFresh: true })
    refreshCacheState.value = {
      loading: false,
      error: null,
      doneAt: new Date().toLocaleString(),
    }
  } catch (e) {
    refreshCacheState.value = {
      loading: false,
      error: e.message,
      doneAt: previousDoneAt,
    }
  }
}

async function loadPipelineStatus() {
  try {
    pipelineState.value.status = await api.pipelineStatus({ forceFresh: true })
    pipelineState.value.error = null
  } catch (e) {
    pipelineState.value.error = e.message
  }
}

async function runPipeline() {
  pipelineState.value.loading = true
  try {
    const res = await api.runPipeline()
    if (res.queued) {
      pipelineState.value.status = { status: 'queued' }
      pipelineState.value.error = null
      return
    }
    if (res.status === 'already_running') {
      await loadPipelineStatus()
      return
    }
    await loadPipelineStatus()
    if (!importOpts.value.dry_run) await store.loadHealth({ forceFresh: true })
  } catch (e) {
    pipelineState.value.error = e.message
  } finally {
    pipelineState.value.loading = false
  }
}

onMounted(async () => {
  await store.loadHealth({ forceFresh: true })
  await loadPipelineStatus()
})
</script>

<style scoped>
/* ── PDF section ──────────────────────────────────────────────────────────── */

/* Wrapper around the button so title tooltip works when button is :disabled */
.pdf-btn-wrapper {
  display: inline-flex;
}

.pipeline-grid {
  display: grid;
  gap: 12px;
}

.setting-row-range {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.range-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.range-label {
  font-size: 11px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.range-select {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  background: var(--card);
  color: var(--text);
  font: inherit;
}

@media (max-width: 640px) {
  .setting-row-range {
    grid-template-columns: 1fr;
  }
}

/* Visual greyed-out look for non-Mac (disabled button already prevents clicks) */
.btn-disabled-look {
  opacity: 0.45;
  cursor: not-allowed;
}

.pdf-unavail-note {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-muted, #888);
  text-align: center;
}

/* Progress bar */
.pdf-progress-bar-wrap {
  height: 4px;
  background: var(--bg, #f0f0f0);
  border-radius: 2px;
  margin-top: 10px;
  overflow: hidden;
}
.pdf-progress-bar {
  height: 100%;
  background: var(--accent, #4e8fff);
  border-radius: 2px;
  transition: width 0.3s ease;
}

/* Currently processing filename */
.pdf-current-file {
  margin-top: 5px;
  font-size: 11px;
  color: var(--text-muted, #888);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Summary badge row */
.pdf-summary-bar {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.pdf-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.pdf-badge-ok   { background: rgba(52,199,89,.15);  color: #1a7a3a; }
.pdf-badge-skip { background: rgba(255,204,0,.18);  color: #7a5c00; }
.pdf-badge-err  { background: rgba(255,59,48,.12);  color: #a0200c; }
.pdf-badge-pend { background: rgba(120,120,128,.1); color: var(--text-muted,#888); }

/* Workspace */
.pdf-workspace {
  margin-top: 12px;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 10px;
  padding: 12px;
  background:
    linear-gradient(180deg, rgba(17,27,43,0.96) 0%, rgba(11,19,33,0.98) 100%);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.04),
    0 18px 36px rgba(5,10,18,0.28);
  backdrop-filter: blur(16px);
}

.pdf-workspace-toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.pdf-search,
.pdf-filter {
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 8px;
  background: rgba(255,255,255,0.04);
  color: rgba(255,255,255,0.92);
  font-size: 12px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}

.pdf-search::placeholder {
  color: rgba(255,255,255,0.40);
}

.pdf-search:focus,
.pdf-filter:focus {
  outline: none;
  border-color: rgba(96,165,250,0.55);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.05),
    0 0 0 3px rgba(59,130,246,0.14);
}

.pdf-search {
  flex: 1 1 220px;
}

.pdf-filter {
  flex: 0 0 auto;
}

.pdf-empty-state {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: rgba(255,255,255,0.62);
  font-size: 12px;
  text-align: center;
}

.pdf-groups {
  margin-top: 12px;
}

.pdf-groups-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.pdf-master-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: rgba(255,255,255,0.72);
}

.pdf-group-card,
.pdf-month-card {
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 10px;
  background: rgba(255,255,255,0.02);
}

.pdf-group-card + .pdf-group-card {
  margin-top: 12px;
}

.pdf-month-list {
  padding: 0 10px 10px;
}

.pdf-month-card + .pdf-month-card {
  margin-top: 8px;
}

.pdf-group-header,
.pdf-month-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: transparent;
  border: 0;
  color: rgba(255,255,255,0.94);
  cursor: pointer;
  text-align: left;
}

.pdf-month-header {
  padding: 12px 14px;
}

.pdf-group-header:hover,
.pdf-month-header:hover {
  background: rgba(255,255,255,0.03);
}

.pdf-group-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-weight: 700;
}

.pdf-group-chevron {
  width: 12px;
  color: rgba(147,197,253,0.9);
  flex: 0 0 auto;
}

.pdf-group-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 11px;
  color: rgba(255,255,255,0.56);
}

.pdf-table-wrap {
  margin: 0 10px 10px;
  overflow: auto;
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 8px;
  background: rgba(7,14,25,0.82);
}

.pdf-table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  font-size: 12px;
}

.pdf-table th {
  padding: 10px 12px;
  text-align: left;
  font-size: 11px;
  color: rgba(191,219,254,0.72);
  text-transform: uppercase;
  letter-spacing: .03em;
  border-bottom: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.03);
}

.pdf-table td {
  padding: 10px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.08);
  vertical-align: middle;
  color: rgba(255,255,255,0.82);
}

.pdf-table tbody tr:hover td {
  background: rgba(59,130,246,0.10);
}

.pdf-table tbody tr:last-child td {
  border-bottom: none;
}

.pdf-checkbox-col {
  width: 38px;
}

.pdf-name-cell {
  min-width: 0;
}

.pdf-name-main {
  font-weight: 600;
  color: rgba(255,255,255,0.95);
  word-break: break-word;
}

.pdf-name-sub {
  margin-top: 2px;
  font-size: 11px;
  color: rgba(255,255,255,0.54);
}

.pdf-folder-pill,
.pdf-status-chip {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.pdf-folder-pill {
  background: rgba(148,163,184,0.14);
  color: rgba(191,219,254,0.90);
}

.pdf-status-new        { background: rgba(148,163,184,0.18); color: rgba(226,232,240,0.95); }
.pdf-status-pending    { background: rgba(59,130,246,0.18); color: #93c5fd; }
.pdf-status-processing { background: rgba(96,165,250,0.24); color: #dbeafe; }
.pdf-status-ok         { background: rgba(34,197,94,0.18); color: #86efac; }
.pdf-status-skipped    { background: rgba(245,158,11,0.18); color: #fcd34d; }
.pdf-status-error      { background: rgba(239,68,68,0.18); color: #fca5a5; }

.pdf-detail-cell {
  max-width: 240px;
  color: rgba(255,255,255,0.62);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pdf-desktop-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pdf-desktop-tools {
  margin-top: 12px;
}

.pdf-workspace-footer {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.pdf-workspace-footer-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pdf-selection-note {
  font-size: 12px;
  color: rgba(255,255,255,0.62);
}

.pdf-workspace :deep(input[type="checkbox"]) {
  accent-color: #60a5fa;
}

@media (max-width: 820px) {
  .pdf-workspace {
    padding: 10px;
  }

  .pdf-workspace-toolbar,
  .pdf-workspace-footer,
  .pdf-groups-toolbar,
  .pdf-group-header,
  .pdf-month-header {
    flex-direction: column;
    align-items: stretch;
  }

  .pdf-group-meta {
    justify-content: space-between;
  }

  .pdf-workspace-footer-actions {
    justify-content: stretch;
  }

  .pdf-workspace-footer-actions .btn {
    width: 100%;
    justify-content: center;
  }
}

@media (min-width: 1024px) {
  .settings-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    align-items: start;
  }
}
</style>
