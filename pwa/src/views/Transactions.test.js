/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const transactions = vi.fn()

const store = {
  years: [2026],
  owners: ['Gandrik'],
  categoryNames: ['Transfer', 'Bills', 'Food'],
  categories: [
    { category: 'Food', category_group: 'Health & Family' },
    { category: 'Bills', category_group: 'Housing & Bills' },
    { category: 'Transfer', category_group: 'System' },
  ],
  categoryMap: {
    Bills: { icon: '🧾' },
    Food: { icon: '🍜' },
    Transfer: { icon: '🔁' },
  },
}

vi.mock('../api/client.js', () => ({
  api: {
    transactions: (...args) => transactions(...args),
    patchCategory: vi.fn(),
    aiQuery: vi.fn(),
  },
}))

vi.mock('../stores/finance.js', () => ({
  useFinanceStore: () => store,
}))

vi.mock('../composables/useLayout.js', () => ({
  useLayout: () => ({
    isDesktop: false,
  }),
}))

import Transactions from './Transactions.vue'

describe('Transactions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    transactions.mockResolvedValue({
      transactions: [
        {
          hash: 'tx-1',
          merchant: 'Warung Makan',
          raw_description: 'Lunch',
          category: '',
          owner: 'Gandrik',
          amount: -25000,
          date: '2026-04-01',
          institution: 'BCA',
        },
      ],
      total: 120,
    })
  })

  it('renders category filter options in alphabetical order', async () => {
    const wrapper = mount(Transactions)
    await flushPromises()

    const categorySelect = wrapper.findAll('select')[4]
    const labels = categorySelect.findAll('option').map((option) => option.text())

    expect(labels).toEqual([
      'All Categories',
      'Uncategorised only',
      'Bills',
      'Food',
      'Transfer',
    ])
  })

  it('offers an uncategorised-only filter and requests uncategorised transactions', async () => {
    const wrapper = mount(Transactions)
    await flushPromises()

    const categorySelect = wrapper.findAll('select')[4]
    expect(categorySelect.find('option[value="__uncategorised__"]').exists()).toBe(true)

    await categorySelect.setValue('__uncategorised__')
    await flushPromises()

    expect(transactions).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 0,
      uncategorised_only: true,
    }, {
      forceFresh: true,
    })
  })

  it('bypasses cached transaction responses so category state stays current', async () => {
    mount(Transactions)
    await flushPromises()

    expect(transactions).toHaveBeenCalledWith({
      limit: 50,
      offset: 0,
    }, {
      forceFresh: true,
    })
  })

  it('uses backend total count so filtered results keep pagination', async () => {
    transactions.mockResolvedValueOnce({
      transactions: [
        {
          hash: 'tx-1',
          merchant: 'Warung Makan',
          raw_description: 'Lunch',
          category: 'Food',
          owner: 'Gandrik',
          amount: -25000,
          date: '2026-04-01',
          institution: 'BCA',
        },
      ],
      total: 120,
    })

    const wrapper = mount(Transactions)
    await flushPromises()

    const pagination = wrapper.find('.pagination')
    expect(pagination.exists()).toBe(true)
    expect(pagination.text()).toContain('1 / 3')
  })

  it('filters transactions by category group', async () => {
    const wrapper = mount(Transactions)
    await flushPromises()

    const groupSelect = wrapper.findAll('select')[3]
    const labels = groupSelect.findAll('option').map((option) => option.text())
    expect(labels).toEqual([
      'All Category Groups',
      'Health & Family',
      'Housing & Bills',
      'System',
    ])

    await groupSelect.setValue('Health & Family')
    await flushPromises()

    expect(transactions).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 0,
      category_group: 'Health & Family',
    }, {
      forceFresh: true,
    })
  })
})
