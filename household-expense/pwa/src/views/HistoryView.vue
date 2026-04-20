<template>
  <div class="p-4">
    <h1 class="text-xl font-bold mb-4">{{ L.history }}</h1>

    <div v-if="transactions.length === 0 && !loading" class="text-center text-gray-500 mt-12">
      {{ L.noTransactions }}
    </div>

    <div class="space-y-2">
      <router-link v-for="txn in transactions" :key="txn.id"
        :to="`/edit/${txn.id}`"
        class="block bg-white rounded-lg border p-3 shadow-sm active:bg-gray-50">
        <div class="flex justify-between items-start">
          <div class="flex-1 min-w-0">
            <div class="text-xs text-gray-500">{{ displayDatetime(txn.txn_datetime) }}</div>
            <div class="font-semibold text-sm truncate">
              {{ txn.merchant || txn.description || txn.category_code }}
            </div>
            <div class="text-xs text-gray-400">
              {{ categoryLabel(txn.category_code) }}
            </div>
          </div>
          <div class="text-right ml-2">
            <div class="font-bold text-blue-700">{{ formatIDR(txn.amount) }}</div>
            <div class="text-xs text-gray-400">{{ paymentLabel(txn.payment_method) }}</div>
          </div>
        </div>
      </router-link>
    </div>

    <div v-if="loading" class="text-center text-gray-400 mt-4">Memuat...</div>

    <button v-if="hasMore && !loading" @click="loadMore"
      class="w-full mt-4 py-2 border rounded-lg text-blue-600 text-sm">
      {{ L.loadMore }}
    </button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { fetchCategories, fetchTransactions } from '../api/client.js'
import { formatIDR, displayDatetime } from '../utils.js'
import labels from '../labels.js'

const L = labels

const transactions = ref([])
const categories = ref([])
const loading = ref(false)
const hasMore = ref(true)
const offset = ref(0)
const LIMIT = 30

function categoryLabel(code) {
  const cat = categories.value.find(c => c.code === code)
  return cat ? cat.label_id : code
}

function paymentLabel(method) {
  const map = { cash: L.cash, transfer: L.transfer, ewallet: L.ewallet }
  return map[method] || method
}

async function load() {
  loading.value = true
  try {
    const rows = await fetchTransactions({ limit: LIMIT, offset: offset.value })
    transactions.value.push(...rows)
    hasMore.value = rows.length === LIMIT
    offset.value += rows.length
  } finally {
    loading.value = false
  }
}

function loadMore() {
  load()
}

onMounted(async () => {
  categories.value = await fetchCategories()
  await load()
})
</script>
