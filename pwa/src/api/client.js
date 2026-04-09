const BASE = '/api'
const API_KEY = import.meta.env.VITE_FINANCE_API_KEY || ''

const AUTH_HEADERS = API_KEY ? { 'X-Api-Key': API_KEY } : {}

async function get(path, params = {}) {
  const url = new URL(BASE + path, location.origin)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v))
    }
  }
  const res = await fetch(url.toString(), { headers: AUTH_HEADERS })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
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

export const api = {
  health:              ()         => get('/health'),
  owners:              ()         => get('/owners'),
  categories:          ()         => get('/categories'),
  transactions:        (p = {})   => get('/transactions', p),
  foreignTransactions: (p = {})   => get('/transactions/foreign', p),
  summaryYears:        ()         => get('/summary/years'),
  summaryYear:         (y)        => get(`/summary/year/${y}`),
  summaryMonth:        (y, m)     => get(`/summary/${y}/${m}`),
  summaryExplanation:  (y, m, p={}) => get(`/summary/${y}/${m}/explanation`, p),
  reviewQueue:         (limit=100)=> get('/review-queue', { limit }),
  saveAlias:           (body)     => post('/alias', body),
  sync:                ()         => post('/sync'),
  importData:          (body={})  => post('/import', body),
  patchCategory:       (hash, body) => patch(`/transaction/${hash}/category`, body),

  // ── Stage 3: Wealth Management ─────────────────────────────────────────────
  wealthSummary:       (p = {})   => get('/wealth/summary', p),
  wealthHistory:       (limit=24) => get('/wealth/history', { limit }),
  wealthExplanation:   (p = {})   => get('/wealth/explanation', p),
  wealthExplanationQuery: (body)  => post('/wealth/explanation/query', body),
  wealthSnapshotDates: ()         => get('/wealth/snapshot/dates'),
  createSnapshot:      (body)     => post('/wealth/snapshot', body),

  getBalances:         (p = {})   => get('/wealth/balances', p),
  upsertBalance:       (body)     => post('/wealth/balances', body),
  deleteBalance:       (id)       => del(`/wealth/balances/${id}`),

  getHoldings:         (p = {})   => get('/wealth/holdings', p),
  upsertHolding:       (body)     => post('/wealth/holdings', body),
  deleteHolding:       (id)       => del(`/wealth/holdings/${id}`),
  carryForwardHoldings:(body)     => post('/wealth/holdings/carry-forward', body),

  getLiabilities:      (p = {})   => get('/wealth/liabilities', p),
  upsertLiability:     (body)     => post('/wealth/liabilities', body),
  deleteLiability:     (id)       => del(`/wealth/liabilities/${id}`),

  // ── PDF Local Processing ────────────────────────────────────────────────────
  // The finance-api (port 8090) proxies these to the bridge (port 9100) so the
  // PWA never needs to open a second origin or use the File System Access API.
  //
  //   pdfLocalFiles()              GET  /api/pdf/local-files
  //   processLocalPdf(folder, fn)  POST /api/pdf/process-local
  //   pdfLocalStatus(jobId)        GET  /api/pdf/local-status/:id
  pdfLocalFiles:   ()                      => get('/pdf/local-files'),
  processLocalPdf: (folder, filename)      => post('/pdf/process-local', { folder, filename }),
  pdfLocalStatus:  (jobId)                 => get(`/pdf/local-status/${jobId}`),
}
