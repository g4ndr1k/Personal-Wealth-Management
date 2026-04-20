<template>
  <div class="p-4 space-y-4">
    <h1 class="text-xl font-bold">{{ isEdit ? L.edit : L.addExpense }}</h1>

    <form @submit.prevent="save" class="space-y-3">
      <!-- Amount -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.amount }}</label>
        <input v-model="form.amount" type="number" inputmode="numeric" min="1"
          class="w-full border rounded-lg px-3 py-2 text-lg" required />
      </div>

      <!-- Category -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.category }}</label>
        <select v-model="form.category_code" class="w-full border rounded-lg px-3 py-2 text-lg" required>
          <option value="" disabled>— Pilih —</option>
          <option v-for="cat in categories" :key="cat.code" :value="cat.code">{{ cat.label_id }}</option>
        </select>
      </div>

      <!-- Merchant -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.merchant }}</label>
        <input v-model="form.merchant" type="text" class="w-full border rounded-lg px-3 py-2" />
      </div>

      <!-- Description -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.description }}</label>
        <input v-model="form.description" type="text" class="w-full border rounded-lg px-3 py-2" />
      </div>

      <!-- Payment method -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.paymentMethod }}</label>
        <div class="flex gap-3">
          <label v-for="opt in paymentOpts" :key="opt.value" class="flex items-center gap-1">
            <input type="radio" v-model="form.payment_method" :value="opt.value" />
            <span class="text-sm">{{ opt.label }}</span>
          </label>
        </div>
      </div>

      <!-- Date & Time -->
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-sm font-medium mb-1">{{ L.date }}</label>
          <input v-model="form.date" type="date" class="w-full border rounded-lg px-3 py-2" required />
        </div>
        <div>
          <label class="block text-sm font-medium mb-1">{{ L.time }}</label>
          <input v-model="form.time" type="time" class="w-full border rounded-lg px-3 py-2" required />
        </div>
      </div>

      <!-- Note -->
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.note }}</label>
        <textarea v-model="form.note" rows="2"
          :placeholder="L.notePlaceholder"
          class="w-full border rounded-lg px-3 py-2"></textarea>
      </div>

      <!-- Buttons -->
      <div class="flex gap-3 pt-2">
        <button type="submit" :disabled="saving"
          class="flex-1 bg-blue-600 text-white rounded-lg py-3 text-lg font-semibold disabled:opacity-50">
          {{ saving ? '...' : L.save }}
        </button>
        <router-link :to="isEdit ? '/riwayat' : '/riwayat'"
          class="flex-1 border rounded-lg py-3 text-center text-lg">
          {{ L.cancel }}
        </router-link>
      </div>
    </form>

    <!-- Delete button (edit mode only) -->
    <button v-if="isEdit" @click="doDelete"
      class="w-full border border-red-300 text-red-600 rounded-lg py-3 text-lg">
      {{ L.delete }}
    </button>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, inject } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchCategories, createTransaction, updateTransaction, deleteTransaction, fetchTransactions } from '../api/client.js'
import { todayLocal, nowLocal, buildTxnDatetime } from '../utils.js'
import labels from '../labels.js'

const L = labels
const route = useRoute()
const router = useRouter()
const showToast = inject('toast')

const categories = ref([])
const saving = ref(false)

const isEdit = computed(() => !!route.params.id)

const form = reactive({
  amount: '',
  category_code: '',
  merchant: '',
  description: '',
  payment_method: 'cash',
  date: todayLocal(),
  time: nowLocal(),
  note: '',
})

const paymentOpts = [
  { value: 'cash', label: L.cash },
  { value: 'transfer', label: L.transfer },
  { value: 'ewallet', label: L.ewallet },
]

onMounted(async () => {
  categories.value = await fetchCategories()

  if (isEdit.value) {
    // Load existing transaction for editing
    const txns = await fetchTransactions({ limit: 200 })
    const txn = txns.find(t => t.id === Number(route.params.id))
    if (txn) {
      form.amount = txn.amount
      form.category_code = txn.category_code
      form.merchant = txn.merchant
      form.description = txn.description
      form.payment_method = txn.payment_method
      form.note = txn.note
      // Parse datetime
      const d = new Date(txn.txn_datetime)
      form.date = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`
      form.time = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
    }
  }
})

async function save() {
  if (!form.amount || !form.category_code) return
  if (!form.merchant && !form.description) return

  saving.value = true
  try {
    const payload = {
      amount: Number(form.amount),
      category_code: form.category_code,
      merchant: form.merchant || '',
      description: form.description || '',
      payment_method: form.payment_method,
      note: form.note || '',
    }

    if (isEdit.value) {
      payload.txn_datetime = buildTxnDatetime(form.date, form.time)
      await updateTransaction(Number(route.params.id), payload)
    } else {
      payload.client_txn_id = crypto.randomUUID()
      payload.txn_datetime = buildTxnDatetime(form.date, form.time)
      await createTransaction(payload)
    }

    showToast(L.savedOk)
    router.push('/riwayat')
  } catch {
    showToast(L.saveFailed, 'error')
  } finally {
    saving.value = false
  }
}

async function doDelete() {
  if (!confirm(L.deleteConfirm)) return
  try {
    await deleteTransaction(Number(route.params.id))
    showToast(L.deletedOk)
    router.push('/riwayat')
  } catch {
    showToast(L.saveFailed, 'error')
  }
}
</script>
