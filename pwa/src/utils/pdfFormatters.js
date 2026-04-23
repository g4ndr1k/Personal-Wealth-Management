// PDF workspace formatting and status helpers — single source of truth.
// Shared between Settings.vue, PdfFileTable.vue, and any future PDF UI.
//
// ── PDF File Schema (data contract) ────────────────────────────────────────
// All functions in this module expect file objects shaped as:
// {
//   key: string,                    // unique id: `${folder}/${relativePath}`
//   folder: string,                 // "pdf_inbox" | "pdf_unlocked"
//   filename: string,               // original filename
//   relativePath: string,           // path relative to folder root
//   relativeDir: string,            // directory portion (empty string if root)
//   sizeKb: number,
//   mtime: number | null,           // unix timestamp (seconds)
//   lastProcessedAt: string | null, // ISO date or null
//   lastStatus: string | null,      // "done" | "error" | "pending" | null (="new")
//   lastError: string,
//   selected: boolean,              // checkbox state (mutated by UI)
//   processingState: string | null, // "processing" | "error" | null
//   processingMeta: string,
//   institutionKey: string,         // e.g. "bca", "cimb-niaga"
//   institutionLabel: string,       // e.g. "BCA", "CIMB Niaga"
//   monthKey: string,               // e.g. "bca:2026-03" or "bca:unknown-period"
//   monthLabel: string,             // e.g. "Mar 2026" or "Unknown Period"
//   monthSortKey: string,           // e.g. "2026-03" or "0000-00"
// }
// ───────────────────────────────────────────────────────────────────────────

export function formatPdfDate(value, includeTime = false) {
  if (!value) return 'Never'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return 'Never'
  return date.toLocaleString([], includeTime
    ? { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatPdfSize(sizeKb) {
  return `${Number(sizeKb || 0).toFixed(1)} KB`
}

export function truncateText(text, max = 80) {
  return text && text.length > max ? `${text.slice(0, max - 1)}…` : (text || '')
}

export function getPdfStatusClass(file) {
  if (file.processingState) return file.processingState
  if (file.lastStatus === 'done') return 'ok'
  if (file.lastStatus === 'error') return 'error'
  if (file.lastStatus === 'pending') return 'pending'
  return 'new'
}

export function getPdfStatusLabel(file) {
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

export function getPdfDetail(file) {
  if (file.processingState === 'processing') return 'Processing now…'
  if (file.processingMeta) return file.processingMeta
  if (file.lastStatus === 'error' && file.lastError) return truncateText(file.lastError, 120)
  if (file.lastStatus === 'done') return 'Processed previously'
  return 'Ready to process'
}

export function isPdfReadyToProcess(file) {
  if (file.processingState === 'processing') return false
  return !file.lastStatus || (file.lastStatus !== 'done' && file.lastStatus !== 'pending' && file.lastStatus !== 'error')
}

// Group files by institution, optionally by period within each institution.
// Returns [{ key, label, fileCount?, months?: [{ key, label, sortKey, files }] }]
export function groupFilesByInstitution(files, { byPeriod = false } = {}) {
  const groups = new Map()
  for (const file of files) {
    const instKey = file.institutionKey
    if (!groups.has(instKey)) {
      groups.set(instKey, {
        key: instKey,
        label: file.institutionLabel,
        fileCount: 0,
        ...(byPeriod ? { months: new Map() } : { files: [] }),
      })
    }
    const inst = groups.get(instKey)
    inst.fileCount += 1

    if (byPeriod) {
      if (!inst.months.has(file.monthKey)) {
        inst.months.set(file.monthKey, { key: file.monthKey, label: file.monthLabel, sortKey: file.monthSortKey, files: [] })
      }
      inst.months.get(file.monthKey).files.push(file)
    } else {
      inst.files.push(file)
    }
  }

  const sortByName = (a, b) => a.relativePath.localeCompare(b.relativePath, undefined, { numeric: true, sensitivity: 'base' })

  return Array.from(groups.values())
    .map(inst => {
      if (byPeriod) {
        return {
          ...inst,
          months: Array.from(inst.months.values())
            .sort((a, b) => b.sortKey.localeCompare(a.sortKey))
            .map(month => ({ ...month, files: month.files.slice().sort(sortByName) })),
        }
      }
      return { ...inst, files: inst.files.slice().sort(sortByName) }
    })
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }))
}
