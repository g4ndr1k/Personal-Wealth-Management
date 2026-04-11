import { createRouter, createWebHistory } from 'vue-router'
import MainDashboard      from '../views/MainDashboard.vue'
import Dashboard          from '../views/Dashboard.vue'
import Transactions       from '../views/Transactions.vue'
import ReviewQueue        from '../views/ReviewQueue.vue'
import ForeignSpend       from '../views/ForeignSpend.vue'
import Settings           from '../views/Settings.vue'
import CategoryDrilldown  from '../views/CategoryDrilldown.vue'
import GroupDrilldown     from '../views/GroupDrilldown.vue'
import Wealth             from '../views/Wealth.vue'
import Holdings           from '../views/Holdings.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/',                    component: MainDashboard,     meta: { title: 'Dashboard' } },
    { path: '/flows',               component: Dashboard,         meta: { title: 'Flows' } },
    { path: '/wealth',              component: Wealth,            meta: { title: 'Wealth' } },
    { path: '/holdings',            component: Holdings,          meta: { title: 'Assets' } },
    { path: '/transactions',        component: Transactions,      meta: { title: 'Txns' } },
    { path: '/review',              component: ReviewQueue,       meta: { title: 'Review' } },
    { path: '/foreign',             component: ForeignSpend,      meta: { title: 'Foreign Spend' } },
    { path: '/settings',            component: Settings,          meta: { title: 'More' } },
    { path: '/group-drilldown',     component: GroupDrilldown,    meta: { title: 'Group Detail' } },
    { path: '/category-drilldown',  component: CategoryDrilldown, meta: { title: 'Category Detail' } },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
