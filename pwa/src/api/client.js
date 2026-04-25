import { queueMutation, cacheGet, cacheGetEntry, cacheSet, cacheClearAll } from '../db/index.js'

const BASE = '/api'
const DEFAULT_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000
const DESKTOP_MIN_WIDTH_PX = 1024
const LAYOUT_STORAGE_KEY = 'pwa_layout_mode'
// Security note: VITE_FINANCE_API_KEY is embedded in the JS bundle at build time and is
// visible to anyone who can load the PWA. This is intentional — the app is only accessible
// via Tailscale, so network-level ACLs are the real auth boundary. Do not reuse this key
// for any other service or store sensitive credentials here.
const API_KEY = import.meta.env.VITE_FINANCE_API_KEY || ''
const CF_CLIENT_ID = import.meta.env.VITE_CF_ACCESS_CLIENT_ID || ''
const CF_CLIENT_SECRET = import.meta.env.VITE_CF_ACCESS_CLIENT_SECRET || ''
if (!API_KEY) console.warn('VITE_FINANCE_API_KEY not set — requests will be unauthenticated')
const AUTH_HEADERS = {
  ...(API_KEY ? { 'X-Api-Key': API_KEY } : {}),
  ...(CF_CLIENT_ID ? { 'CF-Access-Client-Id': CF_CLIENT_ID } : {}),
  ...(CF_CLIENT_SECRET ? { 'CF-Access-Client-Secret': CF_CLIENT_SECRET } : {}),
}

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
      const isHtml = text.trimStart().startsWith('<')
      const detail = isHtml
        ? `HTTP ${res.status} — ${res.url} — ${text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 200)}`
        : (text || res.statusText)
      throw new Error(detail)
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

// Clear sensitive cached financial data when the app is backgrounded
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      cacheClearAll().catch(() => {})
    }
  })
}

async function post(path, body = {}) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    const isHtml = text.trimStart().startsWith('<')
    const detail = isHtml
      ? `Request blocked by network security (HTTP ${res.status}). Check Cloudflare settings or access the app on the local network.`
      : (text || res.statusText)
    throw new Error(detail)
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
    const isHtml = text.trimStart().startsWith('<')
    const detail = isHtml
      ? `Request blocked by network security (HTTP ${res.status}). Check Cloudflare settings or access the app on the local network.`
      : (text || res.statusText)
    throw new Error(detail)
  }
  return res.json()
}

async function del(path) {
  const res = await fetch(BASE + path, { method: 'DELETE', headers: AUTH_HEADERS })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    const isHtml = text.trimStart().startsWith('<')
    const detail = isHtml
      ? `Request blocked by network security (HTTP ${res.status}). Check Cloudflare settings or access the app on the local network.`
      : (text || res.statusText)
    throw new Error(detail)
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

async function put(path, body = {}) {
  const res = await fetch(BASE + path, {
    method: 'PUT',
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
  accounts: (options = {}) => get('/accounts', {}, options),
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
  carryForwardHoldings: (body) => postQueued('/wealth/holdings/rollover', body),

  getLiabilities: (p = {}, options = {}) => get('/wealth/liabilities', p, options),
  upsertLiability: (body) => postQueued('/wealth/liabilities', body),
  deleteLiability: (id) => delQueued(`/wealth/liabilities/${id}`),

  backupStatus: (options = {}) => get('/backups/status', {}, options),
  manualBackup: () => post('/backups/manual'),
  nasSyncStatus: (options = {}) => get('/nas-sync/status', {}, options),
  nasSync: () => post('/nas-sync'),

  getMailRules:  (options = {}) => get('/mail-rules', {}, options),
  addMailRule:   (body)         => post('/mail-rules', body),
  patchMailRule: (id, body)     => patch(`/mail-rules/${id}`, body),
  deleteMailRule:(id)           => del(`/mail-rules/${id}`),

  householdSettings: (options = {}) => get('/household/settings', {}, options),
  createHouseholdCategory: (body) => post('/household/categories', body),
  updateHouseholdCategory: (code, body) => put(`/household/categories/${code}`, body),
  deleteHouseholdCategory: (code) => del(`/household/categories/${code}`),
  updateHouseholdTransactionCategory: (id, body) => put(`/household/transaction/${id}/category`, body),
  updateHouseholdCashPool: (id, body) => put(`/household/cash-pools/${id}`, body),

  preferences: (options = {}) => get('/preferences', {}, options),
  savePreferences: (body) => put('/preferences', body),

  aiQuery: (query) => post('/ai/query', { query }),
  pdfLocalFiles: (options = {}) => get('/pdf/local-files', {}, options),
  pdfLocalWorkspace: (options = {}) => get('/pdf/local-workspace', {}, options),
  processLocalPdf: (folder, relativePath) => post('/pdf/process-local', { folder, relative_path: relativePath }),
  pdfLocalStatus: (jobId, options = {}) => get(`/pdf/local-status/${jobId}`, {}, { forceFresh: true, maxAgeMs: 0, ...options }),
  pdfPreflight: (options = {}) => get('/pdf/preflight', {}, { maxAgeMs: 0, ...options }),
  auditCompleteness: (startMonth = '', endMonth = '', options = {}) => get('/audit/completeness', { start_month: startMonth, end_month: endMonth }, options),
  refreshReferenceData,
  postMultipart,
}
