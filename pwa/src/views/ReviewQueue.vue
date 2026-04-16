<template>
  <div>
    <div class="section-hd">
      🔍 Review Queue
      <span v-if="items.length" style="margin-left:auto;font-size:12px;font-weight:600;color:var(--text-muted)">
        {{ items.length }} remaining
      </span>
      <button
        v-if="!store.autoAiRefine && items.length"
        class="btn btn-ghost btn-sm"
        style="font-size:11px;padding:2px 8px;margin-left:8px"
        :disabled="enriching"
        @click="enrichReviewQueue"
      >{{ enriching ? '🤖 Getting suggestions…' : '✨ Get AI suggestions' }}</button>
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
        💡 Select a transaction to assign a merchant name and category.
        If you turn on "Apply to similar", all uncategorised rows with the same
        raw description will be updated at once.
      </div>

      <ReviewWorkspace
        v-if="isDesktop && items.length"
        :items="items"
        :selected-hash="expandedHash"
        @select="toggle"
      >
        <div v-if="selectedItem">
          <div class="review-desc" style="font-weight:700;font-size:15px;margin-bottom:12px;color:var(--text)">
            {{ selectedItem.raw_description }}
          </div>
          <div class="review-sub" style="margin-bottom:14px">
            {{ selectedItem.date }} · {{ selectedItem.owner }} · {{ selectedItem.institution }}
            <template v-if="countSimilar(selectedItem) > 1">
              · <strong>{{ countSimilar(selectedItem) }} similar</strong>
            </template>
            <span v-if="selectedItem.ollama_suggestion" style="color:var(--accent);font-size:11px;margin-left:4px">🤖</span>
          </div>
          <div class="alias-form">
            <div v-if="store.isReadOnly" class="ro-notice">👁 Read-only — open on Mac to categorise transactions.</div>
            <template v-else>
            <div v-if="selectedItem.ollama_suggestion" style="font-size:11px;color:var(--text-muted);margin-bottom:8px">
              🤖 AI suggested: {{ selectedItem.suggested_merchant }} · {{ selectedItem.ollama_suggestion }}
            </div>
            <div class="form-row">
              <label class="form-label">Merchant name</label>
              <input
                class="form-input"
                v-model="form.merchant"
                placeholder="e.g. Grab Food, Indomaret…"
                @keyup.enter="save(selectedItem)"
              />
            </div>
            <div class="form-row">
              <label class="form-label">Category</label>
              <select class="form-input" v-model="form.category">
                <option value="">— select —</option>
                <option v-for="c in store.categoryNames" :key="c" :value="c">
                  {{ store.categoryMap[c]?.icon || '' }} {{ c }}
                </option>
              </select>
            </div>
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
            <div class="form-row">
              <label class="check-label">
                <input type="checkbox" v-model="form.apply_to_similar" />
                Apply to all {{ countSimilar(selectedItem) }} similar transactions
              </label>
            </div>
            <div class="form-actions">
              <button
                class="btn btn-primary"
                :disabled="!form.merchant || !form.category || saving"
                @click="save(selectedItem)"
              >
                <span v-if="saving"><span class="spinner" style="width:12px;height:12px;border-width:2px"></span></span>
                <span v-else>💾 Save</span>
              </button>
              <button class="btn btn-ghost" :disabled="saving" @click="ignoreItem(selectedItem)">🚫 Ignore</button>
              <button class="btn btn-ghost" @click="expandedHash = null">Cancel</button>
            </div>
            </template>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:40px 0">
          <div class="e-icon">👆</div>
          <div class="e-msg">Select a transaction</div>
          <div class="e-sub">Click an item on the left to review it</div>
        </div>
      </ReviewWorkspace>

      <div v-else-if="items.length">
        <div v-for="item in items" :key="item.hash" class="review-item">
          <div class="review-header" @click="toggle(item)">
            <div class="review-meta">
              <div class="review-desc">{{ item.raw_description }}</div>
              <div class="review-sub">
                {{ item.date }} · {{ item.owner }} · {{ item.institution }}
                <template v-if="countSimilar(item) > 1">
                  · <strong>{{ countSimilar(item) }} similar</strong>
                </template>
                <span v-if="item.ollama_suggestion" style="color:var(--accent);font-size:11px;margin-left:4px">🤖</span>
              </div>
            </div>
            <div class="review-amount" :class="item.amount >= 0 ? 'text-income' : 'text-expense'">{{ fmt(item.amount) }}</div>
          </div>

          <div v-if="expandedHash === item.hash" class="review-body">
            <div class="alias-form">
              <div v-if="store.isReadOnly" class="ro-notice">👁 Read-only — open on Mac to categorise transactions.</div>
              <template v-else>
              <div v-if="item.ollama_suggestion" style="font-size:11px;color:var(--text-muted);margin-bottom:8px">
                🤖 AI suggested: {{ item.suggested_merchant }} · {{ item.ollama_suggestion }}
              </div>
              <div class="form-row">
                <label class="form-label">Merchant name</label>
                <input
                  class="form-input"
                  v-model="form.merchant"
                  placeholder="e.g. Grab Food, Indomaret…"
                  @keyup.enter="save(item)"
                />
              </div>
              <div class="form-row">
                <label class="form-label">Category</label>
                <select class="form-input" v-model="form.category">
                  <option value="">— select —</option>
                  <option v-for="c in store.categoryNames" :key="c" :value="c">
                    {{ store.categoryMap[c]?.icon || '' }} {{ c }}
                  </option>
                </select>
              </div>
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
              <div class="form-row">
                <label class="check-label">
                  <input type="checkbox" v-model="form.apply_to_similar" />
                  Apply to all {{ countSimilar(item) }} similar transactions
                </label>
              </div>
              <div class="form-actions">
                <button
                  class="btn btn-primary"
                  :disabled="!form.merchant || !form.category || saving"
                  @click="save(item)"
                >
                  <span v-if="saving"><span class="spinner" style="width:12px;height:12px;border-width:2px"></span></span>
                  <span v-else>💾 Save</span>
                </button>
                <button class="btn btn-ghost" :disabled="saving" @click="ignoreItem(item)">🚫 Ignore</button>
                <button class="btn btn-ghost" @click="expandedHash = null">Cancel</button>
              </div>
              </template>
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
import { ref, onMounted, computed } from 'vue'
import { api } from '../api/client.js'
import { useFinanceStore } from '../stores/finance.js'
import { formatIDR } from '../utils/currency.js'
import { useLayout } from '../composables/useLayout.js'
import ReviewWorkspace from '../components/ReviewWorkspace.vue'

const store = useFinanceStore()
const { isDesktop } = useLayout()

const LIMIT = 100
const items       = ref([])
const loading     = ref(false)
const loadingMore = ref(false)
const error       = ref(null)
const expandedHash = ref(null)
const saving      = ref(false)
const hasMore     = ref(false)
const enriching   = ref(false)
const toast       = ref('')
let toastTimer    = null

const form = ref({
  merchant:        '',
  category:        '',
  match_type:      'exact',
  apply_to_similar: true,
})

const selectedItem = computed(() =>
  items.value.find(i => i.hash === expandedHash.value) || null
)

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
    merchant:        item.suggested_merchant || item.merchant || titleCase(item.raw_description),
    category:        item.ollama_suggestion  || item.category || '',
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

async function ignoreItem(item) {
  saving.value = true
  try {
    const similar = items.value.filter(x => x.raw_description === item.raw_description)
    await Promise.all(similar.map(tx =>
      api.patchCategory(tx.hash, {
        category: 'Ignored',
        notes: 'Ignored by user from review queue',
        update_alias: false,
      })
    ))

    const removed = similar.length || 1
    items.value = items.value.filter(x => x.raw_description !== item.raw_description)
    expandedHash.value = null
    store.decrementReviewCount(removed)
    showToast(`🚫 Ignored ${removed} row${removed !== 1 ? 's' : ''}`)
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
    // Silently backfill aliases first to catch rows imported before alias existed
    try { await api.backfillAliases() } catch {}
    const data = await api.reviewQueue(LIMIT, { forceFresh: true })
    items.value = data.pending ?? data
    hasMore.value = items.value.length === LIMIT
    if (data.total != null) store.setReviewCount(data.total)

    // Fire enrichment in background — only when auto-refine is on
    if (store.autoAiRefine) {
      enrichReviewQueue()
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function enrichReviewQueue() {
  if (enriching.value) return
  enriching.value = true
  try {
    const result = await api.enrichReviewQueue()
    if ((result.suggested ?? 0) + (result.applied ?? 0) > 0) {
      showToast('🤖 AI suggestions ready — refreshing…')
      const d = await api.reviewQueue(LIMIT, { forceFresh: true })
      items.value = d.pending ?? d
    } else {
      showToast('🤖 No new AI suggestions')
    }
  } catch {}
  finally { enriching.value = false }
}

async function loadMore() {
  loadingMore.value = true
  try {
    const data = await api.reviewQueue(LIMIT * 2, { forceFresh: true })
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
.ro-notice {
  padding: 10px 12px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  color: #1d4ed8;
  font-size: 13px;
  text-align: center;
}
</style>
