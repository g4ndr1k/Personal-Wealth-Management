/** @vitest-environment jsdom */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const saveCategoryDefinition = vi.fn()
const pipelineStatus = vi.fn()
const backupStatus = vi.fn()
const manualBackup = vi.fn()

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
  isReadOnly: false,
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
    backupStatus: (...args) => backupStatus(...args),
    manualBackup: (...args) => manualBackup(...args),
    nasSyncStatus: vi.fn().mockResolvedValue({ configured: false, last_synced_at: null, target: null }),
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
    backupStatus.mockResolvedValue({
      backup_root: '/Users/test/agentic-ai/data/backups',
      hourly: { key: 'hourly', label: 'Hourly', max_sets: 24, count: 12, status: 'ok', latest_at: '2026-04-16T11:00:00', next_due_at: '2026-04-16T12:00:00', latest_file: '/Users/test/agentic-ai/data/backups/hourly/finance_hourly_20260416_110000.db' },
      daily: { key: 'daily', label: 'Daily', max_sets: 31, count: 7, status: 'ok', latest_at: '2026-04-15T00:00:00', next_due_at: '2026-04-16T00:00:00', latest_file: '/Users/test/agentic-ai/data/backups/daily/finance_daily_20260415_000000.db' },
      weekly: { key: 'weekly', label: 'Weekly', max_sets: 5, status: 'ok', count: 2, latest_at: '2026-04-13T00:00:00', next_due_at: '2026-04-20T00:00:00', latest_file: '/Users/test/agentic-ai/data/backups/weekly/finance_weekly_20260413_000000.db' },
      monthly: { key: 'monthly', label: 'Monthly', max_sets: 12, count: 1, status: 'ok', latest_at: '2026-04-01T00:00:00', next_due_at: '2026-05-01T00:00:00', latest_file: '/Users/test/agentic-ai/data/backups/monthly/finance_monthly_20260401_000000.db' },
      manual: { key: 'manual', label: 'Manual', max_sets: 10, count: 3, status: 'ok', latest_at: '2026-04-16T11:30:00', next_due_at: null, latest_file: '/Users/test/agentic-ai/data/backups/manual/finance_manual_20260416_113000.db' },
    })
    manualBackup.mockResolvedValue({ ok: true, path: '/Users/test/agentic-ai/data/backups/manual/finance_manual_20260416_120000.db', created_at: '2026-04-16T12:00:00' })

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

  it('shows backup retention status for each tier', async () => {
    const wrapper = mount(Settings)
    await flushPromises()

    expect(backupStatus).toHaveBeenCalledWith({ forceFresh: true })
    expect(wrapper.text()).toContain('Backup')
    expect(wrapper.text()).toContain('Hourly')
    expect(wrapper.text()).toContain('12 / 24 kept')
    expect(wrapper.text()).toContain('Manual')
    expect(wrapper.text()).toContain('3 / 10 kept')
  })

  it('triggers a manual backup from settings', async () => {
    const wrapper = mount(Settings)
    await flushPromises()

    await wrapper.find('[data-testid="manual-backup-button"]').trigger('click')
    await flushPromises()

    expect(manualBackup).toHaveBeenCalledTimes(1)
    expect(backupStatus).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('finance_manual_20260416_120000.db')
  })
})
