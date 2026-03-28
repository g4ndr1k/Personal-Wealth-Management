<template>
  <div>
    <div class="section-hd">⚙️ Settings</div>

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
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'

const store = useFinanceStore()

const syncState = ref({ loading: false, result: null, error: null })
const importState = ref({ loading: false, result: null, error: null })
const importOpts = ref({ dry_run: false, overwrite: false })

async function doSync() {
  syncState.value = { loading: true, result: null, error: null }
  try {
    const res = await api.sync()
    syncState.value.result = res
    // Refresh health + store data
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
    if (!importOpts.value.dry_run) {
      await store.loadHealth()
    }
  } catch (e) {
    importState.value.error = e.message
  } finally {
    importState.value.loading = false
  }
}

onMounted(() => store.loadHealth())
</script>
