import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../api/client.js'

export const useCoretaxStore = defineStore('coretax', () => {
  // ── State ──────────────────────────────────────────────────────────────
  const taxYear = ref(new Date().getFullYear())
  const summary = ref(null)
  const rows = ref([])
  const mappings = ref([])
  const staging = ref([])
  const stagingBatchId = ref(null)
  const unmatched = ref([])
  const reconcileRuns = ref([])
  const lastReconcileTrace = ref(null)
  const exports = ref([])
  const loading = ref(false)
  const error = ref('')

  // ── Getters ────────────────────────────────────────────────────────────
  const hasRows = computed(() => rows.value.length > 0)
  const assetRows = computed(() => rows.value.filter(r => r.kind === 'asset'))
  const liabilityRows = computed(() => rows.value.filter(r => r.kind === 'liability'))
  const coveragePct = computed(() => summary.value?.coverage_pct ?? 0)
  const filledRows = computed(() => summary.value?.filled_rows ?? 0)
  const totalRows = computed(() => summary.value?.total_rows ?? 0)

  // ── Actions ────────────────────────────────────────────────────────────

  function setTaxYear(year) {
    taxYear.value = year
    // Reset data when year changes
    summary.value = null
    rows.value = []
    unmatched.value = []
    lastReconcileTrace.value = null
    exports.value = []
  }

  async function fetchSummary() {
    loading.value = true
    error.value = ''
    try {
      summary.value = await api.coretaxSummary({ tax_year: taxYear.value })
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function fetchRows(kind) {
    loading.value = true
    error.value = ''
    try {
      const params = { tax_year: taxYear.value }
      if (kind) params.kind = kind
      const result = await api.coretaxRows(params)
      rows.value = result.rows || []
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function fetchMappings() {
    try {
      const result = await api.coretaxMappings()
      mappings.value = result.mappings || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function uploadPriorYear(file) {
    loading.value = true
    error.value = ''
    try {
      const result = await api.coretaxImportPriorYear(file, taxYear.value)
      stagingBatchId.value = result.batch_id
      staging.value = result.rows || []
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchStaging(batchId) {
    loading.value = true
    try {
      const result = await api.coretaxStaging(batchId || stagingBatchId.value)
      staging.value = result.rows || []
      stagingBatchId.value = result.batch_id
    } catch (e) {
      error.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  async function overrideStagingRow(batchId, rowId, carryForward) {
    await api.coretaxStagingOverride(batchId, rowId, carryForward)
    // Update local state
    const idx = staging.value.findIndex(r => r.id === rowId)
    if (idx >= 0) {
      staging.value[idx] = { ...staging.value[idx], user_override_carry_forward: carryForward }
    }
  }

  async function commitStaging(batchId) {
    loading.value = true
    error.value = ''
    try {
      const result = await api.coretaxStagingCommit(batchId || stagingBatchId.value)
      staging.value = []
      stagingBatchId.value = null
      // Refresh rows after commit
      await fetchRows()
      await fetchSummary()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function deleteStagingBatch(batchId) {
    await api.coretaxStagingDelete(batchId || stagingBatchId.value)
    staging.value = []
    stagingBatchId.value = null
  }

  async function updateRow(rowId, updates) {
    const result = await api.coretaxRowPatch(rowId, updates)
    // Update local state
    const idx = rows.value.findIndex(r => r.id === rowId)
    if (idx >= 0) {
      rows.value[idx] = result
    }
    return result
  }

  async function createRow(data) {
    const result = await api.coretaxRowCreate({ ...data, tax_year: taxYear.value })
    await fetchRows()
    return result
  }

  async function removeRow(rowId) {
    await api.coretaxRowDelete(rowId)
    rows.value = rows.value.filter(r => r.id !== rowId)
  }

  async function lockRow(rowId, field, reason) {
    const result = await api.coretaxRowLock(rowId, { field, reason })
    const idx = rows.value.findIndex(r => r.id === rowId)
    if (idx >= 0) rows.value[idx] = result
    return result
  }

  async function unlockRow(rowId, field) {
    const result = await api.coretaxRowUnlock(rowId, { field })
    const idx = rows.value.findIndex(r => r.id === rowId)
    if (idx >= 0) rows.value[idx] = result
    return result
  }

  async function resetFromRules(kind, kodeHarta) {
    loading.value = true
    try {
      const result = await api.coretaxResetFromRules({
        tax_year: taxYear.value, kind, kode_harta: kodeHarta,
      })
      await fetchRows()
      await fetchSummary()
      return result
    } finally {
      loading.value = false
    }
  }

  async function runReconcile(fsRange, snapshotDate) {
    loading.value = true
    error.value = ''
    try {
      const result = await api.coretaxAutoReconcile({
        tax_year: taxYear.value,
        fs_range: fsRange,
        snapshot_date: snapshotDate,
      })
      lastReconcileTrace.value = result.trace || []
      unmatched.value = result.unmatched || []
      await fetchRows()
      await fetchSummary()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchReconcileRuns() {
    try {
      const result = await api.coretaxReconcileRuns({ tax_year: taxYear.value })
      reconcileRuns.value = result.runs || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function fetchUnmatched(runId) {
    try {
      const params = { tax_year: taxYear.value }
      if (runId) params.run_id = runId
      const result = await api.coretaxUnmatched(params)
      unmatched.value = result.unmatched || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function createMapping(data) {
    return await api.coretaxMappingCreate({ ...data, created_from_tax_year: taxYear.value })
  }

  async function removeMapping(mappingId) {
    await api.coretaxMappingDelete(mappingId)
    mappings.value = mappings.value.filter(m => m.id !== mappingId)
  }

  // ── Phase 2: New mapping-first reconciliation actions ────────────────

  const unmappedPwm = ref([])
  const mappingsGrouped = ref([])
  const staleMappings = ref([])
  const lifecycleBuckets = ref({})
  const renameCandidates = ref([])
  const rowComponents = ref([])
  const componentHistory = ref([])

  async function fetchUnmappedPwm(sourceKind) {
    try {
      const params = { year: taxYear.value }
      if (sourceKind) params.source_kind = sourceKind
      const result = await api.coretaxUnmappedPwm(params)
      unmappedPwm.value = result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function fetchMappingsGrouped() {
    try {
      const result = await api.coretaxMappingsGrouped({ year: taxYear.value })
      mappingsGrouped.value = result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function fetchStaleMappings() {
    try {
      const result = await api.coretaxMappingsStale({ year: taxYear.value })
      staleMappings.value = result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function fetchLifecycleMappings(bucket) {
    try {
      const params = { year: taxYear.value }
      if (bucket) params.bucket = bucket
      const result = await api.coretaxMappingsLifecycle(params)
      lifecycleBuckets.value = result.buckets || {}
      // Flatten all bucket items into staleMappings for the lifecycle section
      if (result.items) {
        const all = []
        for (const [bk, items] of Object.entries(result.items)) {
          if (Array.isArray(items)) {
            for (const item of items) {
              item.lifecycle_bucket = bk
              all.push(item)
            }
          }
        }
        staleMappings.value = all
      }
      return result
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function assignMappingDirect(data) {
    require_writable()
    try {
      const result = await api.coretaxMappingAssign({ ...data, year: taxYear.value })
      await fetchMappings()
      await fetchUnmappedPwm()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    }
  }

  async function patchMapping(mappingId, updates) {
    require_writable()
    try {
      const result = await api.coretaxMappingPatch(mappingId, updates)
      await fetchMappings()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    }
  }

  async function confirmMapping(mappingId) {
    require_writable()
    try {
      const result = await api.coretaxMappingConfirm(mappingId)
      await fetchMappings()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    }
  }

  async function suggestMappings(items) {
    try {
      const result = await api.coretaxMappingSuggest({
        year: taxYear.value,
        items: items || null,
      })
      return result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
      return []
    }
  }

  async function suggestPreview(suggestions) {
    try {
      return await api.coretaxMappingSuggestPreview({
        year: taxYear.value,
        suggestions,
      })
    } catch (e) {
      error.value = e?.message || String(e)
      return { preview: [], count: 0 }
    }
  }

  async function suggestReject(data) {
    try {
      return await api.coretaxMappingSuggestReject({
        year: taxYear.value,
        ...data,
      })
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  async function findRenames() {
    try {
      const result = await api.coretaxMappingRenameCandidates({ year: taxYear.value })
      renameCandidates.value = result.items || []
      return result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
      return []
    }
  }

  async function fetchRowComponents(stableKey, runId) {
    try {
      const params = { year: taxYear.value, stable_key: stableKey }
      if (runId) params.run_id = runId
      const result = await api.coretaxRowComponents(params)
      rowComponents.value = result.items || []
      return result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
      return []
    }
  }

  async function fetchComponentHistory(matchKind, matchValue) {
    try {
      const result = await api.coretaxComponentHistory({ match_kind: matchKind, match_value: matchValue })
      componentHistory.value = result.items || []
      return result.items || []
    } catch (e) {
      error.value = e?.message || String(e)
      return []
    }
  }

  async function fetchRunDiff(runId, vs) {
    try {
      const params = { year: taxYear.value, run_id: runId }
      if (vs) params.vs = vs
      return await api.coretaxRunDiff(params)
    } catch (e) {
      error.value = e?.message || String(e)
      return null
    }
  }

  function require_writable() {
    // No-op in frontend — server enforces FINANCE_READ_ONLY
  }

  async function runExport() {
    loading.value = true
    error.value = ''
    try {
      const result = await api.coretaxExport({ tax_year: taxYear.value })
      await fetchExports()
      return result
    } catch (e) {
      error.value = e?.message || String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function fetchExports() {
    try {
      const result = await api.coretaxExports({ tax_year: taxYear.value })
      exports.value = result.exports || []
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }

  return {
    // State
    taxYear, summary, rows, mappings, staging, stagingBatchId,
    unmatched, reconcileRuns, lastReconcileTrace, exports,
    loading, error,
    // Phase 2 state
    unmappedPwm, mappingsGrouped, staleMappings, lifecycleBuckets,
    renameCandidates, rowComponents, componentHistory,
    // Getters
    hasRows, assetRows, liabilityRows, coveragePct, filledRows, totalRows,
    // Actions
    setTaxYear, fetchSummary, fetchRows, fetchMappings,
    uploadPriorYear, fetchStaging, overrideStagingRow,
    commitStaging, deleteStagingBatch,
    updateRow, createRow, removeRow,
    lockRow, unlockRow, resetFromRules,
    runReconcile, fetchReconcileRuns, fetchUnmatched,
    createMapping, removeMapping,
    // Phase 2 actions
    fetchUnmappedPwm, fetchMappingsGrouped, fetchStaleMappings,
    fetchLifecycleMappings, assignMappingDirect, patchMapping,
    confirmMapping, suggestMappings, suggestPreview, suggestReject,
    findRenames, fetchRowComponents, fetchComponentHistory, fetchRunDiff,
    runExport, fetchExports,
  }
})
