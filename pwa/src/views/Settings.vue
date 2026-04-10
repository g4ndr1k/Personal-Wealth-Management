<template>
  <div>
    <div class="section-hd">⚙️ Settings</div>
    <div class="settings-grid">

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
        <button class="btn btn-ghost btn-sm" style="margin-top:12px" @click="store.loadHealth">
          🔄 Refresh status
        </button>
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
        for bank statement PDFs. Each file is sent to the bridge for parsing —
        duplicates are automatically skipped via hash-check.
      </div>

      <!-- Restricted to desktop Mac — tooltip on wrapper for disabled button -->
      <span
        class="pdf-btn-wrapper"
        :title="!isDesktopMac ? 'This feature is only available on the Desktop controller.' : ''"
      >
        <button
          class="btn btn-primary btn-block"
          :disabled="!isDesktopMac || pdf.phase !== 'idle'"
          :class="{ 'btn-disabled-look': !isDesktopMac }"
          @click="doScanAndProcess"
        >
          <span v-if="pdf.phase === 'scanning'">
            <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
            Scanning folders…
          </span>
          <span v-else-if="pdf.phase === 'processing'">
            <span class="spinner" style="width:14px;height:14px;border-width:2px"></span>
            Processing {{ pdf.processed }}&thinsp;/&thinsp;{{ pdf.total }}…
          </span>
          <span v-else>
            🔍 Scan &amp; Process PDFs
          </span>
        </button>
      </span>

      <!-- Non-Mac notice -->
      <div v-if="!isDesktopMac" class="pdf-unavail-note">
        Only available on macOS desktop. Open this app on your Mac controller.
      </div>

      <!-- Progress bar -->
      <div v-if="pdf.phase === 'processing' && pdf.total > 0" class="pdf-progress-bar-wrap">
        <div
          class="pdf-progress-bar"
          :style="{ width: Math.round(100 * pdf.processed / pdf.total) + '%' }"
        ></div>
      </div>

      <!-- Current file -->
      <div v-if="pdf.phase === 'processing' && pdf.current" class="pdf-current-file">
        ↳ {{ pdf.current }}
      </div>

      <!-- Fatal error -->
      <div v-if="pdf.fatalError" class="alert alert-error" style="margin-top:10px">
        ❌ {{ pdf.fatalError }}
      </div>

      <!-- Per-file results -->
      <div v-if="pdf.files.length > 0" style="margin-top:12px">
        <div class="pdf-summary-bar">
          <span class="pdf-badge pdf-badge-ok">✅ {{ pdfCounts.ok }}</span>
          <span v-if="pdfCounts.skipped > 0" class="pdf-badge pdf-badge-skip">⏭ {{ pdfCounts.skipped }} skipped</span>
          <span v-if="pdfCounts.error > 0"   class="pdf-badge pdf-badge-err">❌ {{ pdfCounts.error }} failed</span>
          <span v-if="pdf.phase !== 'idle'"   class="pdf-badge pdf-badge-pend">⏳ {{ pdfCounts.pending }} pending</span>
          <span class="pdf-badge" style="background:rgba(120,120,128,.1);color:var(--text-muted,#888)">
            {{ pdf.total }} total
          </span>
        </div>

        <div class="pdf-file-list">
          <div
            v-for="f in pdf.files"
            :key="f.key"
            :class="['pdf-file-row', `pdf-row-${f.status}`]"
          >
            <span class="pdf-file-icon">{{ FILE_ICONS[f.status] }}</span>
            <div class="pdf-file-info">
              <span class="pdf-file-name">{{ f.filename }}</span>
              <span class="pdf-file-folder">{{ f.folder }}</span>
            </div>
            <span class="pdf-file-meta">{{ f.meta }}</span>
          </div>
        </div>

        <button
          v-if="pdf.phase === 'idle'"
          class="btn btn-ghost btn-sm"
          style="margin-top:8px"
          @click="resetPdf"
        >
          🗑 Clear results
        </button>
      </div>

      <div v-if="isDesktopMac" class="pdf-desktop-tools">
        <div class="setting-desc" style="margin-bottom:10px">
          Desktop-only PDF workspace from the bridge controller.
        </div>
        <div class="pdf-desktop-actions">
          <button class="btn btn-ghost btn-sm" @click="showPdfWorkspace = !showPdfWorkspace">
            {{ showPdfWorkspace ? 'Hide PDF Workspace' : 'Open PDF Workspace' }}
          </button>
          <a
            class="btn btn-ghost btn-sm"
            :href="bridgePdfUiUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            Open in New Tab
          </a>
        </div>
        <div v-if="showPdfWorkspace" class="pdf-ui-wrap">
          <iframe
            class="pdf-ui-frame"
            :src="bridgePdfUiUrl"
            title="PDF Workspace"
            loading="lazy"
          ></iframe>
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

const bridgePdfUiUrl = computed(() =>
  `${window.location.protocol}//${window.location.hostname}:9100/pdf/ui`
)

// ── PDF processing state ─────────────────────────────────────────────────────
const FILE_ICONS = { pending: '⏳', processing: '⚙️', ok: '✅', skipped: '⏭', error: '❌' }

const EMPTY_PDF_STATE = () => ({
  phase:     'idle',   // 'idle' | 'scanning' | 'processing'
  files:     [],       // [{ key, folder, filename, status, meta }]
  current:   '',
  processed: 0,
  total:     0,
  fatalError: null,
})
const pdf = ref(EMPTY_PDF_STATE())

const pdfCounts = computed(() => {
  const c = { ok: 0, skipped: 0, error: 0, pending: 0 }
  for (const f of pdf.value.files) {
    if      (f.status === 'ok')      c.ok++
    else if (f.status === 'skipped') c.skipped++
    else if (f.status === 'error')   c.error++
    else                             c.pending++
  }
  return c
})

function resetPdf() { pdf.value = EMPTY_PDF_STATE() }

// ── Poll /api/pdf/local-status until done (max 3 min) ───────────────────────
async function pollStatus(jobId, timeoutMs = 180_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2500))
    const s = await api.pdfLocalStatus(jobId)
    if (s.status === 'done' || s.status === 'error') return s
  }
  throw new Error('Timed out after 3 min')
}

// ── Main flow: scan server folders → process each file sequentially ──────────
async function doScanAndProcess() {
  pdf.value = EMPTY_PDF_STATE()
  pdf.value.phase = 'scanning'

  // Step 1: ask the finance-api for the list of PDFs on disk
  let discovered = []
  try {
    discovered = await api.pdfLocalFiles()
  } catch (err) {
    pdf.value.fatalError = `Could not list local PDFs: ${err.message}`
    pdf.value.phase = 'idle'
    return
  }

  if (discovered.length === 0) {
    pdf.value.fatalError = 'No PDF files found in pdf_inbox or pdf_unlocked.'
    pdf.value.phase = 'idle'
    return
  }

  // Populate list (all pending)
  pdf.value.files = discovered.map(f => ({
    key:      `${f.folder}/${f.filename}`,
    folder:   f.folder,
    filename: f.filename,
    status:   'pending',
    meta:     `${f.size_kb} KB`,
  }))
  pdf.value.total     = discovered.length
  pdf.value.processed = 0
  pdf.value.phase     = 'processing'

  // Step 2: process sequentially
  for (let i = 0; i < pdf.value.files.length; i++) {
    const f = pdf.value.files[i]
    f.status = 'processing'
    pdf.value.current = f.filename

    try {
      // Submit to bridge via finance-api proxy
      const res = await api.processLocalPdf(f.folder, f.filename)
      const jobId = res.job_id
      if (!jobId) throw new Error('No job_id returned')

      // Poll until bridge finishes
      const final = await pollStatus(jobId)

      if (final.status === 'error') {
        f.status = 'error'
        f.meta   = final.error || 'Parser error'
      } else {
        const log = (final.log || '').toLowerCase()
        if (log.includes('duplicate') || log.includes('skipped') || log.includes('already imported')) {
          f.status = 'skipped'
          f.meta   = 'Already imported'
        } else {
          f.status = 'ok'
          const m = (final.log || '').match(/(?:rows added|upserted|bond|fund)[^\n]*/i)
          f.meta = m ? m[0].trim().slice(0, 60) : 'Imported'
        }
      }
    } catch (err) {
      f.status = 'error'
      f.meta   = err.message.slice(0, 80)
    }

    pdf.value.processed = i + 1
  }

  pdf.value.phase   = 'idle'
  pdf.value.current = ''
}

// ── Existing actions ─────────────────────────────────────────────────────────
async function doSync() {
  syncState.value = { loading: true, result: null, error: null }
  try {
    const res = await api.sync()
    syncState.value.result = res
    await store.loadHealth()
    await store.loadCategories()
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
    importState.value.result = res
    if (!importOpts.value.dry_run) await store.loadHealth()
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

async function loadPipelineStatus() {
  try {
    pipelineState.value.status = await api.pipelineStatus()
    pipelineState.value.error = null
  } catch (e) {
    pipelineState.value.error = e.message
  }
}

async function runPipeline() {
  pipelineState.value.loading = true
  try {
    const res = await api.runPipeline()
    if (res.status === 'already_running') {
      await loadPipelineStatus()
      return
    }
    await loadPipelineStatus()
    if (!importOpts.value.dry_run) await store.loadHealth()
  } catch (e) {
    pipelineState.value.error = e.message
  } finally {
    pipelineState.value.loading = false
  }
}

onMounted(async () => {
  await store.loadHealth()
  await loadPipelineStatus()
})
</script>

<style scoped>
/* ── PDF section ──────────────────────────────────────────────────────────── */

/* Wrapper around the button so title tooltip works when button is :disabled */
.pdf-btn-wrapper {
  display: block;
}

.pipeline-grid {
  display: grid;
  gap: 12px;
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

/* Scrollable file result list */
.pdf-file-list {
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 6px;
  font-size: 12px;
}
.pdf-file-row {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border, #f0f0f0);
}
.pdf-file-row:last-child { border-bottom: none; }

/* Row tint per status */
.pdf-row-ok        { background: rgba(52,199,89,.05); }
.pdf-row-skipped   { background: rgba(255,204,0,.06); }
.pdf-row-error     { background: rgba(255,59,48,.06); }
.pdf-row-processing{ background: rgba(78,143,255,.07); }
.pdf-row-pending   { background: transparent; }

.pdf-file-icon { font-size: 13px; }
.pdf-file-info {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}
.pdf-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text, #222);
}
.pdf-file-folder {
  font-size: 10px;
  color: var(--text-muted, #aaa);
  white-space: nowrap;
}
.pdf-file-meta {
  font-size: 10px;
  color: var(--text-muted, #888);
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: right;
}

.pdf-desktop-tools {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border, #e0e0e0);
}

.pdf-desktop-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.pdf-ui-wrap {
  margin-top: 12px;
  border: 1px solid var(--border, #e0e0e0);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}

.pdf-ui-frame {
  display: block;
  width: 100%;
  min-height: 720px;
  border: 0;
  background: #fff;
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
