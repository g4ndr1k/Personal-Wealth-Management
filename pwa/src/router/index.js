import { createRouter, createWebHistory } from 'vue-router'

const MainDashboard = () => import('../views/MainDashboard.vue')
const Dashboard = () => import('../views/Dashboard.vue')
const Transactions = () => import('../views/Transactions.vue')
const ReviewQueue = () => import('../views/ReviewQueue.vue')
const ForeignSpend = () => import('../views/ForeignSpend.vue')
const Settings = () => import('../views/Settings.vue')
const CategoryDrilldown = () => import('../views/CategoryDrilldown.vue')
const GroupDrilldown = () => import('../views/GroupDrilldown.vue')
const Wealth = () => import('../views/Wealth.vue')
const Holdings = () => import('../views/Holdings.vue')
const AuditCompleteness = () => import('../views/AuditCompleteness.vue')

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: MainDashboard, meta: { title: 'Dashboard', keepAlive: true } },
    { path: '/flows', name: 'flows', component: Dashboard, meta: { title: 'Flows', keepAlive: true } },
    { path: '/wealth', name: 'wealth', component: Wealth, meta: { title: 'Wealth', keepAlive: true } },
    { path: '/holdings', name: 'holdings', component: Holdings, meta: { title: 'Assets', keepAlive: true } },
    { path: '/transactions', name: 'transactions', component: Transactions, meta: { title: 'Txns', keepAlive: true } },
    { path: '/review', name: 'review', component: ReviewQueue, meta: { title: 'Review', keepAlive: true } },
    { path: '/foreign', name: 'foreign', component: ForeignSpend, meta: { title: 'Foreign Spend' } },
    { path: '/settings', name: 'settings', component: Settings, meta: { title: 'More' } },
    { path: '/audit', name: 'audit', component: AuditCompleteness, meta: { title: 'Audit' } },
    { path: '/group-drilldown', name: 'group-drilldown', component: GroupDrilldown, meta: { title: 'Group Detail' } },
    { path: '/category-drilldown', name: 'category-drilldown', component: CategoryDrilldown, meta: { title: 'Category Detail' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
