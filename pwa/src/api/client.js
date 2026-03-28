const BASE = '/api'

async function get(path, params = {}) {
  const url = new URL(BASE + path, location.origin)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v))
    }
  }
  const res = await fetch(url.toString())
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res.json()
}

async function post(path, body = {}) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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
  reviewQueue:         (limit=100)=> get('/review-queue', { limit }),
  saveAlias:           (body)     => post('/alias', body),
  sync:                ()         => post('/sync'),
  importData:          (body={})  => post('/import', body),
}
