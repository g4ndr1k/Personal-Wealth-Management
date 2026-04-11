import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { registerSW } from 'virtual:pwa-register'
import router from './router/index.js'
import App from './App.vue'
import './style.css'

const updateSW = registerSW({
  immediate: true,
  onNeedRefresh() {
    updateSW(true)
  },
  onOfflineReady() {},
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
