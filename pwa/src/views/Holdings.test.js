/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const getBalances = vi.fn()
const getHoldings = vi.fn()
const wealthSnapshotDates = vi.fn()
const carryForwardHoldings = vi.fn()

const store = {
  owners: ['Gandrik'],
}

vi.mock('../api/client.js', () => ({
  api: {
    getBalances: (...args) => getBalances(...args),
    getHoldings: (...args) => getHoldings(...args),
    wealthSnapshotDates: (...args) => wealthSnapshotDates(...args),
    carryForwardHoldings: (...args) => carryForwardHoldings(...args),
    deleteBalance: vi.fn(),
    deleteHolding: vi.fn(),
    deleteLiability: vi.fn(),
    upsertBalance: vi.fn(),
    upsertHolding: vi.fn(),
    upsertLiability: vi.fn(),
    createSnapshot: vi.fn(),
  },
}))

vi.mock('../stores/finance.js', () => ({
  useFinanceStore: () => store,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
}))

import Holdings from './Holdings.vue'

describe('Holdings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    wealthSnapshotDates.mockResolvedValue(['2026-04-30'])
    getBalances.mockResolvedValue([])
    getHoldings.mockResolvedValue([
      { id: 1, asset_group: 'Real Estate', asset_class: 'real_estate', asset_name: 'Grogol 2', market_value_idr: 100107549, last_appraised_date: '2026-03-02' },
    ])
    carryForwardHoldings.mockResolvedValue({ carried: 0 })
  })

  it('bypasses cached holdings responses so newly added assets appear immediately', async () => {
    mount(Holdings)
    await flushPromises()

    expect(getBalances).toHaveBeenCalledWith({ snapshot_date: '2026-04-30' }, { forceFresh: true })
    expect(getHoldings).toHaveBeenCalledWith({ snapshot_date: '2026-04-30' }, { forceFresh: true })
  })
})
