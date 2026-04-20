<template>
  <div class="min-h-screen flex items-center justify-center p-6">
    <form @submit.prevent="doLogin" class="w-full max-w-sm space-y-4">
      <h1 class="text-2xl font-bold text-center mb-6">{{ L.appName }}</h1>
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.username }}</label>
        <input v-model="username" type="text" autocomplete="username"
          class="w-full border rounded-lg px-3 py-2 text-lg" required />
      </div>
      <div>
        <label class="block text-sm font-medium mb-1">{{ L.password }}</label>
        <input v-model="password" type="password" autocomplete="current-password"
          class="w-full border rounded-lg px-3 py-2 text-lg" required />
      </div>
      <p v-if="error" class="text-red-600 text-sm">{{ error }}</p>
      <button type="submit" :disabled="loading"
        class="w-full bg-blue-600 text-white rounded-lg py-3 text-lg font-semibold disabled:opacity-50">
        {{ loading ? '...' : L.login }}
      </button>
    </form>
  </div>
</template>

<script setup>
import { ref, inject } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api/client.js'
import labels from '../labels.js'

const L = labels
const router = useRouter()
const showToast = inject('toast')

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function doLogin() {
  error.value = ''
  loading.value = true
  try {
    const user = await login(username.value, password.value)
    localStorage.setItem('household_logged_in', 'true')
    localStorage.setItem('household_user', user.display_name)
    showToast(labels.welcome.replace('{name}', user.display_name))
    router.push('/tambah')
  } catch (e) {
    error.value = L.loginFailed
  } finally {
    loading.value = false
  }
}
</script>
