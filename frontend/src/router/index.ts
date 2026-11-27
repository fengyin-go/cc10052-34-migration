import { createRouter, createWebHistory } from 'vue-router'

import MigrationWorkbenchView from '@/views/MigrationWorkbenchView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/migration', name: 'migration', component: MigrationWorkbenchView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
