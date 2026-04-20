// API client — all requests go to same origin (served by FastAPI)
const BASE = '/api/household'

async function request(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    credentials: 'same-origin',
    ...opts,
  })
  if (res.status === 401) {
    localStorage.removeItem('household_logged_in')
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || res.statusText)
  }
  if (res.status === 204) return null
  return res.json()
}

export function login(username, password) {
  return request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}

export function fetchCategories() {
  return request('/categories')
}

export function createTransaction(data) {
  return request('/transactions', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export function fetchTransactions(params = {}) {
  const qs = new URLSearchParams(params).toString()
  return request(`/transactions?${qs}`)
}

export function updateTransaction(id, data) {
  return request(`/transactions/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export function deleteTransaction(id) {
  return request(`/transactions/${id}`, { method: 'DELETE' })
}
