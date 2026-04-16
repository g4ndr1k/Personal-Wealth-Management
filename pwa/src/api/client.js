import { queueMutation, cacheGet, cacheGetEntry, cacheSet, cacheClearAll } from '../db/index.js'

const BASE = '/api'
const DEFAULT_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000
const DESKTOP_MIN_WIDTH_PX = 1024
const LAYOUT_STORAGE_KEY = 'pwa_layout_mode'
const API_KEY = import.meta.env.VITE_FINANCE_API_KEY || ''
const AUTH_HEADERS = API_KEY
  ? { 'X-Api-Key': API_KEY }
  : (console.warn('VITE_FINANCE_API_KEY not set — requests will be unauthenticated'), {})

function isNetworkError(error) {
  return !navigator.onLine || error?.name === 'TypeError'
}

function getCacheKey(url) {
  return `GET:${url.pathname}${url.search}`
}

function readLayoutMode() {
  try {
    const mode = window.localStorage.getItem(LAYOUT_STORAGE_KEY)
    if (mode === 'desktop' || mode === 'mobile' || mode === 'auto') return mode
  } catch {}
  return 'auto'
}

function isDesktopLayout() {
  if (typeof window === 'undefined') return false
  const mode = readLayoutMode()
  if (mode === 'desktop') return true
  if (mode === 'mobile') return false
  if (typeof window.matchMedia === 'function') {
    return window.matchMedia(`(min-width: ${DESKTOP_MIN_WIDTH_PX}px)`).matches
  }
  return typeof window.innerWidth === 'number' && window.innerWidth >= DESKTOP_MIN_WIDTH_PX
}

function shouldUseLongLivedGetCache(options = {}) {
  if ((options.maxAgeMs ?? DEFAULT_CACHE_MAX_AGE_MS) <= 0) return false
  return !isDesktopLayout()
}

async function get(path, params = {}, options = {}) {
  const url = new URL(BASE + path, location.origin)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v))
    }
  }
  const cacheKey = getCacheKey(url)
  const maxAgeMs = options.maxAgeMs ?? DEFAULT_CACHE_MAX_AGE_MS
  const forceFresh = options.forceFresh === true
  const useLongLivedGetCache = shouldUseLongLivedGetCache(options)

  if (!forceFresh && useLongLivedGetCache) {
    const cachedEntry = await cacheGetEntry(cacheKey)
    if (cachedEntry && (Date.now() - cachedEntry.updatedAt) < maxAgeMs) {
      return cachedEntry.value
    }
  }

  try {
    const res = await fetch(url.toString(), { headers: AUTH_HEADERS })
    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`${res.status}: ${text || res.statusText}`)
    }
    const payload = await res.json()
    await cacheSet(cacheKey, payload)
    return payload
  } catch (error) {
    if (!isNetworkError(error)) throw error
    const cached = await cacheGet(cacheKey)
    if (cached !== null) {
      console.warn(`[Cache] Using cached GET for ${url.pathname}:`, error.message)
      return cached
    }
    throw error
  }
}

async function refreshReferenceData() {
  await cacheClearAll()
}

async function post(path, body = {}) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

async function postQueued(path, body = {}) {
  try {
    return await post(path, body)
  } catch (error) {
    if (!isNetworkError(error)) throw error
    await queueMutation('POST', BASE + path, body, AUTH_HEADERS)
    return { queued: true }
  }
}

// Multipart upload — do NOT set Content-Type; browser injects the boundary automatically.
async function postMultipart(path, formData) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { ...AUTH_HEADERS },
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

async function del(path) {
  const res = await fetch(BASE + path, { method: 'DELETE', headers: AUTH_HEADERS })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

async function delQueued(path) {
  try {
    return await del(path)
  } catch (error) {
    if (!isNetworkError(error)) throw error
    await queueMutation('DELETE', BASE + path, undefined, AUTH_HEADERS)
    return { queued: true }
  }
}

async function patch(path, body = {}) {
  const res = await fetch(BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

async function patchQueued(path, body = {}) {
  try {
    return await patch(path, body)
  } catch (error) {
    if (!isNetworkError(error)) throw error
    await queueMutation('PATCH', BASE + path, body, AUTH_HEADERS)
    const transaction = { category: body.category }
    if (Object.prototype.hasOwnProperty.call(body, 'notes')) {
      transaction.notes = body.notes
    }
    return { queued: true, transaction }
  }
}

export const api = {
  health: (options = {}) => get('/health', {}, options),
  owners: (options = {}) => get('/owners', {}, options),
  categories: (options = {}) => get('/categories', {}, options),
  saveCategoryDefinition: (body) => post('/categories', body),
  transactions: (p = {}, options = {}) => get('/transactions', p, options),
  foreignTransactions: (p = {}, options = {}) => get('/transactions/foreign', p, options),
  summaryYears: (options = {}) => get('/summary/years', {}, options),
  summaryYear: (y, options = {}) => get(`/summary/year/${y}`, {}, options),
  summaryMonth: (y, m, options = {}) => get(`/summary/${y}/${m}`, {}, options),
  summaryExplanation: (y, m, p = {}, options = {}) => get(`/summary/${y}/${m}/explanation`, p, options),
  summaryExplanationQuery: (y, m, body) => post(`/summary/${y}/${m}/explanation/query`, body),
  reviewQueue: (limit = 100, options = {}) => get('/review-queue', { limit }, options),
  enrichReviewQueue: () => post('/review-queue/suggest'),
  saveAlias: (body) => postQueued('/alias', body),
  backfillAliases: () => postQueued('/backfill-aliases'),
  sync: () => postQueued('/sync'),
  importData: (body = {}) => postQueued('/import', body),
  pipelineStatus: (options = {}) => get('/pipeline/status', {}, options),
  runPipeline: () => postQueued('/pipeline/run'),
  patchCategory: (hash, body) => patchQueued(`/transaction/${hash}/category`, body),

  wealthSummary: (p = {}, options = {}) => get('/wealth/summary', p, options),
  wealthHistory: (limit = 24, options = {}) => get('/wealth/history', { limit }, options),
  wealthExplanation: (p = {}, options = {}) => get('/wealth/explanation', p, options),
  wealthExplanationQuery: (body) => post('/wealth/explanation/query', body),
  wealthSnapshotDates: (options = {}) => get('/wealth/snapshot/dates', {}, options),
  createSnapshot: (body) => postQueued('/wealth/snapshot', body),

  getBalances: (p = {}, options = {}) => get('/wealth/balances', p, options),
  upsertBalance: (body) => postQueued('/wealth/balances', body),
  deleteBalance: (id) => delQueued(`/wealth/balances/${id}`),

  getHoldings: (p = {}, options = {}) => get('/wealth/holdings', p, options),
  upsertHolding: (body) => postQueued('/wealth/holdings', body),
  deleteHolding: (id) => delQueued(`/wealth/holdings/${id}`),
  carryForwardHoldings: (body) => postQueued('/wealth/holdings/carry-forward', body),

  getLiabilities: (p = {}, options = {}) => get('/wealth/liabilities', p, options),
  upsertLiability: (body) => postQueued('/wealth/liabilities', body),
  deleteLiability: (id) => delQueued(`/wealth/liabilities/${id}`),

  aiQuery: (query) => post('/ai/query', { query }),
  pdfLocalFiles: (options = {}) => get('/pdf/local-files', {}, options),
  pdfLocalWorkspace: (options = {}) => get('/pdf/local-workspace', {}, options),
  processLocalPdf: (folder, relativePath) => post('/pdf/process-local', { folder, relative_path: relativePath }),
  pdfLocalStatus: (jobId, options = {}) => get(`/pdf/local-status/${jobId}`, {}, options),
  auditCompleteness: (startMonth = '', endMonth = '', options = {}) => get('/audit/completeness', { start_month: startMonth, end_month: endMonth }, options),
  refreshReferenceData,
  postMultipart,
}
