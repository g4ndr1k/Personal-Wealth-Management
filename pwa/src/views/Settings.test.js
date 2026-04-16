/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const saveCategoryDefinition = vi.fn()
const pipelineStatus = vi.fn()

const store = {
  health: { status: 'ok', transaction_count: 100, needs_review: 2, last_sync: '2026-04-16 09:00:00' },
  dashboardStartMonth: '2026-01',
  dashboardEndMonth: '2026-04',
  dashboardRangeLabel: 'Jan 2026 - Apr 2026',
  dashboardMonthOptions: [
    { value: '2026-01', label: 'Jan 2026' },
    { value: '2026-04', label: 'Apr 2026' },
  ],
  categories: [
    { category: 'Food', icon: '🍜', sort_order: 10, is_recurring: 0, monthly_budget: 500000, category_group: 'Living', subcategory: 'Dining' },
    { category: 'Transfer', icon: '🔁', sort_order: 90, is_recurring: 0, monthly_budget: null, category_group: 'System', subcategory: '' },
  ],
  loadHealth: vi.fn().mockResolvedValue(undefined),
  loadCategories: vi.fn().mockResolvedValue(undefined),
  setDashboardRange: vi.fn(),
  bootstrap: vi.fn().mockResolvedValue(undefined),
}

vi.mock('../stores/finance.js', () => ({
  useFinanceStore: () => store,
}))

vi.mock('../api/client.js', () => ({
  api: {
    saveCategoryDefinition: (...args) => saveCategoryDefinition(...args),
    pipelineStatus: (...args) => pipelineStatus(...args),
    refreshReferenceData: vi.fn().mockResolvedValue(undefined),
    sync: vi.fn(),
    importData: vi.fn(),
    runPipeline: vi.fn(),
    pdfLocalStatus: vi.fn(),
    pdfLocalWorkspace: vi.fn(),
    processLocalPdf: vi.fn(),
  },
}))

import Settings from './Settings.vue'

describe('Settings category editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pipelineStatus.mockResolvedValue({ status: 'idle', last_run_at: null, next_scheduled_at: null, last_result: null })
    saveCategoryDefinition.mockResolvedValue({ category: 'Dining Out' })

    Object.defineProperty(window.navigator, 'platform', {
      configurable: true,
      value: 'MacIntel',
    })
    Object.defineProperty(window.navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (Macintosh; Intel Mac OS X)',
    })
    Object.defineProperty(window.navigator, 'maxTouchPoints', {
      configurable: true,
      value: 0,
    })
  })

  it('loads an existing category into the editor', async () => {
    const wrapper = mount(Settings)
    await flushPromises()

    await wrapper.find('[data-testid="category-preset-select"]').setValue('Food')
    await flushPromises()

    expect(wrapper.find('[data-testid="category-name-input"]').element.value).toBe('Food')
    expect(wrapper.find('[data-testid="category-icon-input"]').element.value).toBe('🍜')
    expect(wrapper.find('[data-testid="category-group-input"]').element.value).toBe('Living')
  })

  it('saves a new category and refreshes store categories', async () => {
    const wrapper = mount(Settings)
    await flushPromises()

    await wrapper.find('[data-testid="category-preset-select"]').setValue('__new__')
    await wrapper.find('[data-testid="category-name-input"]').setValue('Dining Out')
    await wrapper.find('[data-testid="category-icon-input"]').setValue('🍽️')
    await wrapper.find('[data-testid="category-sort-order-input"]').setValue('15')
    await wrapper.find('[data-testid="category-budget-input"]').setValue('750000')
    await wrapper.find('[data-testid="category-group-input"]').setValue('Living')
    await wrapper.find('[data-testid="category-subcategory-input"]').setValue('Meals')
    await wrapper.find('[data-testid="category-recurring-input"]').setValue(true)
    await wrapper.find('[data-testid="category-save-button"]').trigger('click')
    await flushPromises()

    expect(saveCategoryDefinition).toHaveBeenCalledWith({
      original_category: '',
      category: 'Dining Out',
      icon: '🍽️',
      sort_order: 15,
      monthly_budget: 750000,
      category_group: 'Living',
      subcategory: 'Meals',
      is_recurring: true,
    })
    expect(store.loadCategories).toHaveBeenCalledWith({ forceFresh: true })
  })
})
