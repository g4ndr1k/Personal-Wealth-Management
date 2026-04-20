import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import LoginView from './views/LoginView.vue'
import AddView from './views/AddView.vue'
import HistoryView from './views/HistoryView.vue'
import './style.css'

const routes = [
  { path: '/', redirect: '/tambah' },
  { path: '/login', component: LoginView, meta: { public: true } },
  { path: '/tambah', component: AddView },
  { path: '/riwayat', component: HistoryView },
  { path: '/edit/:id', component: AddView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.public) return true
  if (!localStorage.getItem('household_logged_in')) return '/login'
  return true
})

const app = createApp(App)
app.use(router)
app.mount('#app')
