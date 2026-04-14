/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const queueMutation = vi.fn()
const cacheGet = vi.fn()
const cacheGetEntry = vi.fn()
const cacheSet = vi.fn()
const cacheClearAll = vi.fn()

vi.mock('../db/index.js', () => ({
  queueMutation,
  cacheGet,
  cacheGetEntry,
  cacheSet,
  cacheClearAll,
}))

function jsonResponse(payload, init = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    statusText: init.statusText ?? 'OK',
    json: vi.fn().mockResolvedValue(payload),
    text: vi.fn().mockResolvedValue(init.text ?? ''),
  }
}

describe('api client offline GET fallback', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    cacheGetEntry.mockResolvedValue(null)
    Object.defineProperty(window.navigator, 'onLine', {
      configurable: true,
      value: true,
    })
  })

  it('returns cached GET data when the network is unavailable', async () => {
    const cachedPayload = [{ id: 1, description: 'cached transaction' }]
    cacheGet.mockResolvedValueOnce(cachedPayload)
    global.fetch = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'))

    const { api } = await import('./client.js')
    const result = await api.transactions({ year: 2026, owner: 'Gandrik' })

    expect(result).toEqual(cachedPayload)
    expect(cacheGet).toHaveBeenCalledWith('GET:/api/transactions?year=2026&owner=Gandrik')
  })

  it('writes successful GET responses into the offline cache', async () => {
    const payload = { status: 'ok' }
    global.fetch = vi.fn().mockResolvedValueOnce(jsonResponse(payload))

    const { api } = await import('./client.js')
    const result = await api.health()

    expect(result).toEqual(payload)
    expect(cacheSet).toHaveBeenCalledWith('GET:/api/health', payload)
  })

  it('returns fresh cached GET data without hitting the network', async () => {
    const payload = { status: 'cached' }
    cacheGetEntry.mockResolvedValueOnce({ value: payload, updatedAt: Date.now() })
    global.fetch = vi.fn()

    const { api } = await import('./client.js')
    const result = await api.health()

    expect(result).toEqual(payload)
    expect(global.fetch).not.toHaveBeenCalled()
  })
})
