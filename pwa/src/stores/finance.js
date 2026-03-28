import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../api/client.js'

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

  // Bootstrap: called once from App.vue on mount
  async function bootstrap() {
    await Promise.all([loadHealth(), loadOwners(), loadCategories(), loadYears()])
  }

  return {
    // state
    owners, categories, years, health, reviewCount,
    selectedYear, selectedMonth, selectedOwner,
    // computed
    categoryMap, categoryNames,
    // actions
    loadHealth, loadOwners, loadCategories, loadYears,
    decrementReviewCount, bootstrap,
  }
})
