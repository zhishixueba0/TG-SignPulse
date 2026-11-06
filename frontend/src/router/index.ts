import { createRouter, createWebHistory } from 'vue-router'
import Layout from '../views/Layout.vue'
import { useAuthStore } from '../stores/auth'
import { resolveAuthRedirect } from '../lib/auth-guard'
import { isChunkLoadError, reloadOnceForChunkError } from '../lib/chunk-reload'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: Layout,
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
        { path: 'accounts', name: 'accounts', component: () => import('../views/Accounts.vue') },
        { path: 'chats', name: 'chats', component: () => import('../views/Chats.vue') },
        { path: 'proxies', name: 'proxies', component: () => import('../views/Proxies.vue') },
        { path: 'tasks', name: 'tasks', component: () => import('../views/Tasks.vue') },
        { path: 'plugins', name: 'plugins', component: () => import('../views/Plugins.vue') },
        { path: 'logs', name: 'logs', component: () => import('../views/Logs.vue') },
        {
          path: 'settings',
          name: 'settings',
          component: () => import('../views/Settings.vue'),
          beforeEnter: (to) => {
            const tab = to.query.tab
            const isPluginsTab = Array.isArray(tab) ? tab.includes('plugins') : tab === 'plugins'
            // 兼容旧版书签或外部链接: /settings?tab=plugins 或带 testPlugin 参数，重定向到独立的 /plugins，并剥离仅用于旧页面的 tab 参数
            if (isPluginsTab || to.query.testPlugin) {
              const { tab: _tab, ...restQuery } = to.query
              return { name: 'plugins', query: restQuery, replace: true }
            }
          }
        }
      ]
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue')
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('../views/NotFound.vue')
    }
  ]
})

router.beforeEach((to, from) => {
  // 2026-11-06 导航诊断：生产构建 devLog 静默，直接写 console，定位空白卡在哪一步。
  // eslint-disable-next-line no-console
  console.info('[SignPulse:nav] before', String(from.name ?? ''), '->', String(to.name ?? ''))
  const authStore = useAuthStore()
  return resolveAuthRedirect(typeof to.name === 'string' ? to.name : null, authStore)
})

router.afterEach((to, from) => {
  // eslint-disable-next-line no-console
  console.info('[SignPulse:nav] after', String(from.name ?? ''), '->', String(to.name ?? ''))
})

// chunk 加载失败（部署后旧 chunk 文件名已不存在）时硬刷新一次取新版入口，
// 避免导航失败后页面空白卡死；60 秒内只刷新一次，防循环。
router.onError((error) => {
  // eslint-disable-next-line no-console
  console.error('[SignPulse:nav] router error', error)
  if (isChunkLoadError(error)) {
    reloadOnceForChunkError()
  }
})

export default router
