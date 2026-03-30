<template>
  <div>
    <div class="section-hd">
      🔍 Review Queue
      <span v-if="items.length" style="margin-left:auto;font-size:12px;font-weight:600;color:var(--text-muted)">
        {{ items.length }} remaining
      </span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading"><div class="spinner"></div> Loading…</div>

    <!-- Error -->
    <div v-else-if="error" class="alert alert-error">
      ❌ {{ error }}
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" @click="load">Retry</button>
    </div>

    <!-- Empty state -->
    <div v-else-if="!items.length" class="empty-state">
      <div class="e-icon">✅</div>
      <div class="e-msg">All caught up!</div>
      <div class="e-sub">No transactions need review right now.</div>
    </div>

    <!-- Queue items -->
    <div v-else>
      <div class="alert alert-info" style="font-size:12px">
        💡 Tap a transaction to assign a merchant name and category.
        If you turn on "Apply to similar", all uncategorised rows with the same
        raw description will be updated at once.
      </div>

      <div v-for="item in items" :key="item.hash" class="review-item">
        <!-- Header: click to expand -->
        <div class="review-header" @click="toggle(item)">
          <div class="review-meta">
            <div class="review-desc">{{ item.raw_description }}</div>
            <div class="review-sub">
              {{ item.date }} · {{ item.owner }} · {{ item.institution }}
              <template v-if="countSimilar(item) > 1">
                · <strong>{{ countSimilar(item) }} similar</strong>
              </template>
            </div>
          </div>
          <div
            class="review-amount"
            :class="item.amount >= 0 ? 'text-income' : 'text-expense'"
          >{{ fmt(item.amount) }}</div>
        </div>

        <!-- Expanded alias form -->
        <div v-if="expandedHash === item.hash" class="review-body">
          <div class="alias-form">
            <!-- Merchant name -->
            <div class="form-row">
              <label class="form-label">Merchant name</label>
              <input
                class="form-input"
                v-model="form.merchant"
                placeholder="e.g. Grab Food, Indomaret…"
                @keyup.enter="save(item)"
              />
            </div>

            <!-- Category -->
            <div class="form-row">
              <label class="form-label">Category</label>
              <select class="form-input" v-model="form.category">
                <option value="">— select —</option>
                <option v-for="c in store.categoryNames" :key="c" :value="c">
                  {{ store.categoryMap[c]?.icon || '' }} {{ c }}
                </option>
              </select>
            </div>

            <!-- Match type -->
            <div class="form-row">
              <label class="form-label">Match type</label>
              <div class="radio-group">
                <label class="radio-label">
                  <input type="radio" v-model="form.match_type" value="exact" /> Exact
                </label>
                <label class="radio-label">
                  <input type="radio" v-model="form.match_type" value="contains" /> Contains
                </label>
                <label class="radio-label">
                  <input type="radio" v-model="form.match_type" value="regex" /> Regex
                </label>
              </div>
            </div>

            <!-- Apply to similar -->
            <div class="form-row">
              <label class="check-label">
                <input type="checkbox" v-model="form.apply_to_similar" />
                Apply to all {{ countSimilar(item) }} similar transactions
              </label>
            </div>

            <!-- Actions -->
            <div class="form-actions">
              <button
                class="btn btn-primary"
                :disabled="!form.merchant || !form.category || saving"
                @click="save(item)"
              >
                <span v-if="saving"><span class="spinner" style="width:12px;height:12px;border-width:2px"></span></span>
                <span v-else>💾 Save</span>
              </button>
              <button class="btn btn-ghost" @click="expandedHash = null">Cancel</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Load more -->
      <div v-if="hasMore" class="load-more">
        <button class="btn btn-ghost" :disabled="loadingMore" @click="loadMore">
          <span v-if="loadingMore"><span class="spinner" style="width:14px;height:14px;border-width:2px"></span> Loading…</span>
          <span v-else>Load more</span>
        </button>
      </div>
    </div>

    <!-- Toast notification -->
    <Transition name="toast">
      <div v-if="toast" class="toast">{{ toast }}</div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'

const store = useFinanceStore()

const LIMIT = 100
const items       = ref([])
const loading     = ref(false)
const loadingMore = ref(false)
const error       = ref(null)
const expandedHash = ref(null)
const saving      = ref(false)
const hasMore     = ref(false)
const toast       = ref('')
let toastTimer    = null

const form = ref({
  merchant:        '',
  category:        '',
  match_type:      'exact',
  apply_to_similar: true,
})

// ── Helpers ──────────────────────────────────────────────────────────────────
function fmt(n) {
  return formatIDR(n)
}

function countSimilar(item) {
  return items.value.filter(x => x.raw_description === item.raw_description).length
}

function showToast(msg) {
  toast.value = msg
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 3000)
}

function titleCase(str) {
  return (str || '').toLowerCase().replace(/(?:^|\s)\S/g, c => c.toUpperCase())
}

// ── Interactions ─────────────────────────────────────────────────────────────
function toggle(item) {
  if (expandedHash.value === item.hash) {
    expandedHash.value = null
    return
  }
  expandedHash.value = item.hash
  form.value = {
    merchant:        item.merchant || titleCase(item.raw_description),
    category:        item.category || '',
    match_type:      'exact',
    apply_to_similar: true,
  }
}

async function save(item) {
  if (!form.value.merchant || !form.value.category) return
  saving.value = true
  try {
    const result = await api.saveAlias({
      hash:             item.hash,
      alias:            item.raw_description,
      merchant:         form.value.merchant,
      category:         form.value.category,
      match_type:       form.value.match_type,
      apply_to_similar: form.value.apply_to_similar,
    })

    // Remove affected rows from the local list
    let removed = 0
    if (form.value.apply_to_similar) {
      const desc = item.raw_description
      const before = items.value.length
      items.value = items.value.filter(x => x.raw_description !== desc)
      removed = before - items.value.length
    } else {
      items.value = items.value.filter(x => x.hash !== item.hash)
      removed = 1
    }

    expandedHash.value = null
    store.decrementReviewCount(removed)

    const n = result.updated_count ?? removed
    showToast(`✅ Saved! Updated ${n} row${n !== 1 ? 's' : ''}`)
  } catch (e) {
    showToast(`❌ Error: ${e.message}`)
  } finally {
    saving.value = false
  }
}

// ── Data loading ─────────────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value   = null
  try {
    const data = await api.reviewQueue(LIMIT)
    items.value = data.pending ?? data
    hasMore.value = items.value.length === LIMIT
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadMore() {
  loadingMore.value = true
  try {
    const data = await api.reviewQueue(LIMIT * 2)
    items.value = data.pending ?? data
    hasMore.value = false
  } catch (e) {
    showToast(`❌ ${e.message}`)
  } finally {
    loadingMore.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toast-enter-active, .toast-leave-active { transition: opacity 0.2s, transform 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(10px); }
</style>
