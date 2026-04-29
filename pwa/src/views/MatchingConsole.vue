<template>
  <div :class="['settings-page', { 'settings-page--desktop': isDesktop }]">
    <div v-if="!isDesktop" class="section-hd">
      <span class="settings-head-icon" v-html="NAV_SVGS.Matching"></span> Matching Console
    </div>

    <nav v-if="isDesktop" class="settings-sub-nav">
      <div class="settings-sub-nav__title">Matching Console</div>
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
        <span class="settings-head-icon" v-html="NAV_SVGS.Matching"></span>
        Matching Console
        <button class="btn btn--sm" @click="refresh" :disabled="loading">
          {{ loading ? 'Loading…' : 'Refresh' }}
        </button>
      </div>

      <div :key="isDesktop ? activeTab : 'all'" class="settings-grid">

        <!-- ═══ Overview ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'overview'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Dashboard"></span>
            Cross-Domain Stats
          </div>
          <div class="setting-desc">
            Total mappings across all engine domains.
            <span v-if="stats">
              <strong>{{ stats.total_mappings }}</strong> total ·
              <strong>{{ stats.shadow_diff_count }}</strong> shadow diffs pending.
            </span>
          </div>

          <div v-if="stats" class="table-wrap" style="margin-top:12px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Domain</th>
                  <th class="th-num">Total</th>
                  <th class="th-num">Auto</th>
                  <th class="th-num">Manual</th>
                  <th>Last Updated</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="d in stats.domains"
                  :key="d.domain"
                  class="row-link"
                  @click="selectDomain(d.domain)"
                >
                  <td><span class="domain-badge" :class="`domain-badge--${d.domain}`">{{ d.domain }}</span></td>
                  <td class="cell-num">{{ d.total }}</td>
                  <td class="cell-num"><span class="badge-auto">{{ d.auto }}</span></td>
                  <td class="cell-num">{{ d.manual }}</td>
                  <td class="cell-muted">{{ fmtDate(d.last_updated) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!stats && !loading" class="cell-muted" style="margin-top:12px">
            No data — click Refresh.
          </div>
        </div>

        <!-- ═══ Mappings ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'mappings'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.CoreTax"></span>
            Mappings
          </div>

          <div class="setting-row" style="gap:8px;flex-wrap:wrap;margin-bottom:10px">
            <select class="range-select" v-model="selectedDomain" @change="loadMappings">
              <option v-for="d in DOMAINS" :key="d" :value="d">{{ d }}</option>
            </select>
            <select class="range-select" v-model="sourceFilter" @change="loadMappings">
              <option value="">All sources</option>
              <option value="auto_safe">auto_safe</option>
              <option value="manual">manual</option>
            </select>
            <span class="badge" v-if="mappingsResult">{{ mappingsResult.total }} total</span>
          </div>

          <div v-if="mappingsResult && mappingsResult.items.length" class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Raw (identity)</th>
                  <th>Target</th>
                  <th class="th-num">Score</th>
                  <th>Source</th>
                  <th>Confirmed</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="m in mappingsResult.items" :key="m.id">
                  <td class="cell-desc" :title="m.identity_raw">{{ truncate(m.identity_raw, 60) }}</td>
                  <td class="cell-desc" :title="m.target_key">{{ truncate(m.target_key, 40) }}</td>
                  <td class="cell-num">{{ pct(m.confidence_score) }}</td>
                  <td><span class="source-chip" :class="`source-chip--${m.source}`">{{ m.source }}</span></td>
                  <td class="cell-num">{{ m.times_confirmed }}</td>
                  <td>
                    <button class="btn btn--sm btn--danger" @click="deleteMapping(m)" title="Delete mapping">✕</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else-if="!loadingMappings" class="cell-muted" style="margin-top:10px">
            No mappings for <strong>{{ selectedDomain }}</strong>{{ sourceFilter ? ` (${sourceFilter})` : '' }}.
          </div>

          <div v-if="mappingsResult && mappingsResult.next_cursor" style="margin-top:10px">
            <button class="btn btn--sm" @click="loadNextPage">Load more</button>
          </div>
        </div>

        <!-- ═══ Shadow Diffs ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'shadow'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Audit"></span>
            Shadow Diffs
            <span class="badge" v-if="shadowResult">{{ shadowResult.total }}</span>
          </div>
          <div class="setting-desc">
            Disagreements between the categorization engine's Tier-1 cache and the legacy
            4-layer result. Zero rows here is the cutover signal for enabling engine true mode.
          </div>

          <div class="setting-row" style="margin:10px 0">
            <select class="range-select" v-model="shadowFilter" @change="loadShadowDiffs">
              <option value="">All diff classes</option>
              <option value="both_diff">both_diff</option>
              <option value="merchant_diff">merchant_diff</option>
              <option value="category_diff">category_diff</option>
            </select>
          </div>

          <div v-if="shadowResult && shadowResult.items.length" class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Description</th>
                  <th>Legacy</th>
                  <th>Engine</th>
                  <th>Diff</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in shadowResult.items" :key="r.id">
                  <td class="cell-desc">{{ truncate(r.raw_description, 50) }}</td>
                  <td class="cell-desc">{{ r.legacy_merchant }} / {{ r.legacy_category }}</td>
                  <td class="cell-desc">{{ r.engine_merchant }} / {{ r.engine_category }}</td>
                  <td><span class="diff-chip" :class="`diff-chip--${r.diff_class}`">{{ r.diff_class }}</span></td>
                  <td class="cell-muted">{{ truncate(r.run_id, 20) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else-if="!loadingShadow" class="cell-muted" style="margin-top:10px">
            No shadow diffs recorded. Engine and legacy are in full agreement.
          </div>
        </div>

        <!-- ═══ Invariant Log ═══ -->
        <div class="setting-card" v-show="!isDesktop || activeTab === 'log'">
          <div class="setting-title">
            <span class="setting-title-icon" v-html="NAV_SVGS.Audit"></span>
            Invariant Log
          </div>
          <div class="setting-desc">
            CRITICAL and WARNING events from the matching engine. CRITICALs abort the
            per-row operation; WARNINGs log and continue.
          </div>

          <div class="setting-row" style="margin:10px 0;gap:8px;flex-wrap:wrap">
            <select class="range-select" v-model="logSeverity" @change="loadInvariantLog">
              <option value="">All severities</option>
              <option value="CRITICAL">CRITICAL</option>
              <option value="WARNING">WARNING</option>
              <option value="INFO">INFO</option>
            </select>
            <select class="range-select" v-model="logDomain" @change="loadInvariantLog">
              <option value="">All domains</option>
              <option v-for="d in DOMAINS" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>

          <div v-if="invariantLog && invariantLog.items.length" class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Domain</th>
                  <th>Message</th>
                  <th>Run</th>
                  <th>At</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in invariantLog.items" :key="r.id">
                  <td><span class="sev-chip" :class="`sev-chip--${r.severity}`">{{ r.severity }}</span></td>
                  <td><span class="domain-badge" :class="`domain-badge--${r.domain}`">{{ r.domain }}</span></td>
                  <td class="cell-desc">{{ truncate(r.message, 80) }}</td>
                  <td class="cell-muted">{{ truncate(r.run_id, 20) }}</td>
                  <td class="cell-muted">{{ fmtDate(r.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else-if="!loadingLog" class="cell-muted" style="margin-top:10px">
            No invariant log entries.
          </div>
        </div>

      </div><!-- settings-grid -->
    </div><!-- settings-content -->
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'
import { NAV_SVGS } from '../utils/icons.js'
import { useLayout } from '../composables/useLayout.js'

const { isDesktop } = useLayout()

const DOMAINS = ['coretax', 'parser', 'dedup', 'categorization']

const tabs = [
  { id: 'overview',  label: 'Overview',  icon: NAV_SVGS.Dashboard },
  { id: 'mappings',  label: 'Mappings',  icon: NAV_SVGS.CoreTax },
  { id: 'shadow',    label: 'Shadow',    icon: NAV_SVGS.Audit },
  { id: 'log',       label: 'Log',       icon: NAV_SVGS.Audit },
]

const activeTab      = ref('overview')
const loading        = ref(false)
const loadingMappings = ref(false)
const loadingShadow  = ref(false)
const loadingLog     = ref(false)

const stats          = ref(null)
const mappingsResult = ref(null)
const shadowResult   = ref(null)
const invariantLog   = ref(null)

const selectedDomain = ref('coretax')
const sourceFilter   = ref('')
const shadowFilter   = ref('')
const logSeverity    = ref('')
const logDomain      = ref('')

// cursor stack for "load more"
const cursorStack    = ref([])

async function loadStats() {
  try { stats.value = await api.matchingStats() } catch { /* ignore */ }
}

async function loadMappings() {
  loadingMappings.value = true
  cursorStack.value = []
  try {
    const params = { limit: 50 }
    if (sourceFilter.value) params.source = sourceFilter.value
    mappingsResult.value = await api.matchingMappings(selectedDomain.value, params)
  } catch { mappingsResult.value = null }
  finally { loadingMappings.value = false }
}

async function loadNextPage() {
  if (!mappingsResult.value?.next_cursor) return
  loadingMappings.value = true
  try {
    const params = { limit: 50, cursor: mappingsResult.value.next_cursor }
    if (sourceFilter.value) params.source = sourceFilter.value
    const next = await api.matchingMappings(selectedDomain.value, params)
    mappingsResult.value = {
      ...next,
      items: [...mappingsResult.value.items, ...next.items],
    }
  } catch { /* ignore */ }
  finally { loadingMappings.value = false }
}

async function loadShadowDiffs() {
  loadingShadow.value = true
  try {
    const params = { limit: 100 }
    if (shadowFilter.value) params.diff_class = shadowFilter.value
    shadowResult.value = await api.matchingShadowDiffs(params)
  } catch { shadowResult.value = null }
  finally { loadingShadow.value = false }
}

async function loadInvariantLog() {
  loadingLog.value = true
  try {
    const params = { limit: 100 }
    if (logSeverity.value) params.severity = logSeverity.value
    if (logDomain.value)   params.domain   = logDomain.value
    invariantLog.value = await api.matchingInvariantLog(params)
  } catch { invariantLog.value = null }
  finally { loadingLog.value = false }
}

async function refresh() {
  loading.value = true
  await Promise.all([loadStats(), loadMappings(), loadShadowDiffs(), loadInvariantLog()])
  loading.value = false
}

async function deleteMapping(m) {
  if (!confirm(`Delete mapping id=${m.id} (${truncate(m.identity_raw, 40)} → ${truncate(m.target_key, 30)})?`)) return
  try {
    await api.matchingDeleteMapping(selectedDomain.value, m.id)
    await loadMappings()
    await loadStats()
  } catch { /* ignore */ }
}

function selectDomain(d) {
  selectedDomain.value = d
  activeTab.value = 'mappings'
  loadMappings()
}

function truncate(s, n) {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n) + '…'
}

function pct(v) {
  return v != null ? (v * 100).toFixed(0) + '%' : '—'
}

function fmtDate(s) {
  if (!s) return '—'
  try { return s.slice(0, 16).replace('T', ' ') } catch { return s }
}

onMounted(refresh)
</script>

<style scoped>
.domain-badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  background: var(--color-surface2, #f0f0f0);
  color: var(--color-text, #333);
}
.domain-badge--coretax        { background: #dbeafe; color: #1d4ed8; }
.domain-badge--parser         { background: #dcfce7; color: #15803d; }
.domain-badge--dedup          { background: #fef9c3; color: #92400e; }
.domain-badge--categorization { background: #fce7f3; color: #9d174d; }

.source-chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 11px;
  background: var(--color-surface2, #f0f0f0);
}
.source-chip--auto_safe { background: #dcfce7; color: #15803d; }
.source-chip--manual    { background: #dbeafe; color: #1d4ed8; }

.badge-auto {
  display: inline-block;
  padding: 0 5px;
  border-radius: 8px;
  font-size: 11px;
  background: #dcfce7;
  color: #15803d;
}

.diff-chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 11px;
  background: #fee2e2;
  color: #991b1b;
}
.diff-chip--merchant_diff { background: #fef9c3; color: #78350f; }
.diff-chip--category_diff { background: #fce7f3; color: #9d174d; }

.sev-chip {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 600;
}
.sev-chip--CRITICAL { background: #fee2e2; color: #991b1b; }
.sev-chip--WARNING  { background: #fef9c3; color: #78350f; }
.sev-chip--INFO     { background: #f0f9ff; color: #0369a1; }

.row-link { cursor: pointer; }
.row-link:hover td { background: var(--color-surface2, #f8f8f8); }

.cell-muted { color: var(--color-muted, #888); font-size: 12px; }
.cell-num   { text-align: right; font-variant-numeric: tabular-nums; }
.cell-desc  { max-width: 260px; word-break: break-word; }

.btn--sm    { padding: 3px 10px; font-size: 12px; }
.btn--danger { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
.btn--danger:hover { background: #fca5a5; }

.th-num { text-align: right; }

/* ── Two-column desktop shell ──────────────────────────────────────────── */
.settings-page--desktop {
  display: grid;
  grid-template-columns: 240px 1fr;
  align-items: start;
  min-height: 100%;
}

.settings-content { min-width: 0; }

/* ── Left nav — pixel-match DesktopSidebar.vue ─────────────────────────── */
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

/* Shared icon sizes for header */
.settings-head-icon {
  width: 16px;
  height: 16px;
  margin-right: 8px;
  vertical-align: middle;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.settings-head-icon :deep(svg) { width: 16px; height: 16px; }

.setting-title-icon {
  width: 14px;
  height: 14px;
  margin-right: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--primary);
  opacity: 0.9;
  vertical-align: middle;
  position: relative;
  top: -1px;
}
.setting-title-icon :deep(svg) { width: 14px; height: 14px; }

.settings-page--desktop .settings-grid {
  grid-template-columns: 1fr !important;
}
</style>
