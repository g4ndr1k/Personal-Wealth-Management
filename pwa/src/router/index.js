import { createRouter, createWebHistory } from 'vue-router'
import Dashboard    from '../views/Dashboard.vue'
import Transactions from '../views/Transactions.vue'
import ReviewQueue  from '../views/ReviewQueue.vue'
import ForeignSpend from '../views/ForeignSpend.vue'
import Settings     from '../views/Settings.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/',             component: Dashboard,    meta: { title: 'Dashboard' } },
    { path: '/transactions', component: Transactions, meta: { title: 'Transactions' } },
    { path: '/review',       component: ReviewQueue,  meta: { title: 'Review Queue' } },
    { path: '/foreign',      component: ForeignSpend, meta: { title: 'Foreign Spend' } },
    { path: '/settings',     component: Settings,     meta: { title: 'Settings' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
