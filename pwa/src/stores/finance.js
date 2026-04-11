import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { api } from '../api/client.js'

const DASHBOARD_MIN_MONTH = '2026-01'
const DASHBOARD_START_KEY = 'finance.dashboard.startMonth'
const DASHBOARD_END_KEY = 'finance.dashboard.endMonth'

function safeStorageGet(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeStorageSet(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // ignore storage failures
  }
}

function normalizeDashboardMonth(value, fallback) {
  return /^\d{4}-\d{2}$/.test(value || '') && value >= DASHBOARD_MIN_MONTH ? value : fallback
}

export const useFinanceStore = defineStore('finance', () => {
  // ── Shared reference data ────────────────────────────────────────────────
  const owners     = ref([])
  const categories = ref([])
  const years      = ref([])
  const health     = ref(null)
  const reviewCount = ref(0)

  // ── Navigation state (shared across views) ───────────────────────────────
  const now = new Date()
  const selectedYear  = ref(now.getFullYear())
  const selectedMonth = ref(now.getMonth() + 1)
  const selectedOwner = ref('')   // '' = all owners
  const currentMonthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const dashboardStartMonth = ref(normalizeDashboardMonth(safeStorageGet(DASHBOARD_START_KEY), DASHBOARD_MIN_MONTH))
  const dashboardEndMonth = ref(normalizeDashboardMonth(safeStorageGet(DASHBOARD_END_KEY), currentMonthKey))

  // ── Derived ──────────────────────────────────────────────────────────────
  const categoryMap = computed(() => {
    const m = {}
    for (const c of categories.value) m[c.category] = c
    return m
  })

  const categoryNames = computed(() =>
    categories.value
      .slice()
      .sort((a, b) => a.sort_order - b.sort_order)
      .map(c => c.category)
  )

  const dashboardMonthOptions = computed(() => {
    const [startYear, startMonth] = DASHBOARD_MIN_MONTH.split('-').map(Number)
    const [endYear, endMonth] = currentMonthKey.split('-').map(Number)
    const options = []
    const cursor = new Date(startYear, startMonth - 1, 1)
    const end = new Date(endYear, endMonth - 1, 1)
    const labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    while (cursor <= end) {
      const year = cursor.getFullYear()
      const month = cursor.getMonth() + 1
      const value = `${year}-${String(month).padStart(2, '0')}`
      options.push({ value, label: `${labels[month - 1]} ${year}` })
      cursor.setMonth(cursor.getMonth() + 1)
    }
    return options
  })

  const dashboardRangeLabel = computed(() => {
    const lookup = new Map(dashboardMonthOptions.value.map(option => [option.value, option.label]))
    return `${lookup.get(dashboardStartMonth.value) || dashboardStartMonth.value} - ${lookup.get(dashboardEndMonth.value) || dashboardEndMonth.value}`
  })

  // ── Actions ──────────────────────────────────────────────────────────────
  async function loadHealth() {
    try {
      health.value = await api.health()
      reviewCount.value = health.value.needs_review ?? 0
    } catch (e) {
      console.warn('health check failed:', e.message)
    }
  }

  async function loadOwners() {
    try {
      owners.value = await api.owners()
    } catch (e) {
      console.warn('loadOwners failed:', e.message)
    }
  }

  async function loadCategories() {
    try {
      const cats = await api.categories()
      categories.value = cats.sort((a, b) => a.sort_order - b.sort_order)
    } catch (e) {
      console.warn('loadCategories failed:', e.message)
    }
  }

  async function loadYears() {
    try {
      years.value = await api.summaryYears()
    } catch (e) {
      console.warn('loadYears failed:', e.message)
    }
  }

  function decrementReviewCount(n = 1) {
    reviewCount.value = Math.max(0, reviewCount.value - n)
  }

  function setDashboardRange(startMonth, endMonth) {
    const normalizedStart = normalizeDashboardMonth(startMonth, DASHBOARD_MIN_MONTH)
    const normalizedEnd = normalizeDashboardMonth(endMonth, currentMonthKey)
    if (normalizedStart <= normalizedEnd) {
      dashboardStartMonth.value = normalizedStart
      dashboardEndMonth.value = normalizedEnd
    } else {
      dashboardStartMonth.value = normalizedEnd
      dashboardEndMonth.value = normalizedStart
    }
  }

  watch(dashboardStartMonth, (value) => {
    safeStorageSet(DASHBOARD_START_KEY, value)
    if (value > dashboardEndMonth.value) dashboardEndMonth.value = value
  })

  watch(dashboardEndMonth, (value) => {
    safeStorageSet(DASHBOARD_END_KEY, value)
    if (value < dashboardStartMonth.value) dashboardStartMonth.value = value
  })

  // Bootstrap: called once from App.vue on mount
  async function bootstrap() {
    await Promise.all([loadHealth(), loadOwners(), loadCategories(), loadYears()])
  }

  return {
    // state
    owners, categories, years, health, reviewCount,
    selectedYear, selectedMonth, selectedOwner,
    dashboardStartMonth, dashboardEndMonth,
    // computed
    categoryMap, categoryNames, dashboardMonthOptions, dashboardRangeLabel,
    // actions
    loadHealth, loadOwners, loadCategories, loadYears,
    decrementReviewCount, setDashboardRange, bootstrap,
  }
})
