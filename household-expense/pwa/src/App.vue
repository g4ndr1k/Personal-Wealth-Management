<template>
  <div class="min-h-screen flex flex-col max-w-lg mx-auto">
    <main class="flex-1 pb-16 overflow-auto">
      <router-view />
    </main>
    <nav v-if="showNav" class="fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 max-w-lg mx-auto">
      <div class="flex justify-around py-2">
        <router-link to="/tambah" class="flex flex-col items-center text-xs px-3 py-1"
          :class="$route.path === '/tambah' ? 'text-blue-600' : 'text-gray-500'">
          <span class="text-xl">➕</span>
          <span>Tambah</span>
        </router-link>
        <router-link to="/riwayat" class="flex flex-col items-center text-xs px-3 py-1"
          :class="$route.path.startsWith('/riwayat') ? 'text-blue-600' : 'text-gray-500'">
          <span class="text-xl">📋</span>
          <span>Riwayat</span>
        </router-link>
        <button @click="doLogout" class="flex flex-col items-center text-xs px-3 py-1 text-gray-500">
          <span class="text-xl">🚪</span>
          <span>Keluar</span>
        </button>
      </div>
    </nav>
    <div v-if="toast" class="fixed top-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg shadow-lg text-white text-sm z-50"
      :class="toastType === 'error' ? 'bg-red-500' : 'bg-green-600'">
      {{ toast }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { logout as apiLogout } from './api/client.js'
import labels from './labels.js'

const router = useRouter()
const route = useRoute()

const toast = ref('')
const toastType = ref('ok')
let toastTimer = null

const showNav = computed(() => {
  return route.path !== '/login'
})

function showToast(msg, type = 'ok') {
  toast.value = msg
  toastType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 2500)
}

provide('toast', showToast)

async function doLogout() {
  try { await apiLogout() } catch {}
  localStorage.removeItem('household_logged_in')
  router.push('/login')
}
</script>
