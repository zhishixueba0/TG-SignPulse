<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, onErrorCaptured, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getAuthToken } from '../lib/api/core'
import { getAppVersion } from '../lib/api'
import {
  LayoutDashboard,
  Users,
  MessageSquareText,
  Network,
  Zap,
  Puzzle,
  Terminal,
  Settings,
  UserCircle,
  Github,
  Globe,
  Moon,
  Sun,
  Menu,
  X,
} from 'lucide-vue-next'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import { useToast } from '../composables/useToast'
import { lockBodyScroll, unlockBodyScroll } from '../lib/body-scroll-lock'
import UserProfileModal from '../components/settings/UserProfileModal.vue'
import { devLog } from '../lib/devLog'
import { createViewPrefetcher } from '../lib/view-prefetch'

const route = useRoute()
const router = useRouter()
const { isDark, toggleTheme } = useTheme()
const { locale, toggleLanguage, t } = useI18n()
const toast = useToast()
const isMobileMenuOpen = ref(false)
const showProfileModal = ref(false)
const sidebarVersion = ref('')

// 子页面渲染错误边界：子页面 setup/render 抛错时 router.onError 接不住，
// 没有边界会整片空白。这里捕获后显示错误卡片（带原文案 + 重试），
// 不再静默空白；返回 false 阻止继续冒泡到全局 handler，避免重复上报。
const viewError = ref<string | null>(null)
const viewKey = ref(0)
onErrorCaptured((err: unknown) => {
  const message = err instanceof Error ? err.message : String(err ?? '')
  viewError.value = message || 'Unknown error'
  // 生产构建 devLog 默认静默，这里必须直接写 console，否则线上看不到。
  // eslint-disable-next-line no-console
  console.error('[SignPulse] 子页面渲染失败', err)
  toast.error(t('common.viewRenderFailed'))
  return false
})

// 切到其他页面时清掉旧的渲染错误，让新页面正常挂载。
watch(
  () => route.name,
  () => {
    viewError.value = null
  },
)

const retryView = () => {
  viewError.value = null
  viewKey.value += 1
}

const reloadPage = () => {
  window.location.reload()
}
const menuButtonRef = ref<HTMLButtonElement | null>(null)
const drawerCloseButtonRef = ref<HTMLButtonElement | null>(null)

// lg 断点（1023px）以下视为移动端：侧栏是抽屉，关闭时应同步对读屏隐藏
const mobileQuery = window.matchMedia('(max-width: 1023px)')
const isMobileView = ref(mobileQuery.matches)
const onViewportChange = () => {
  isMobileView.value = mobileQuery.matches
}
const sidebarHidden = computed(() => isMobileView.value && !isMobileMenuOpen.value)

const loadSidebarVersion = async () => {
  const token = getAuthToken()
  if (!token) return
  try {
    const info = await getAppVersion(token)
    sidebarVersion.value = info.version ? `v${info.version}` : ''
  } catch {
    sidebarVersion.value = ''
  }
}

const goSettingsAbout = () => {
  isMobileMenuOpen.value = false
  router.push({ name: 'settings' })
}

const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && isMobileMenuOpen.value) {
    isMobileMenuOpen.value = false
  }
}

let menuScrollLocked = false
watch(isMobileMenuOpen, async (open, prev) => {
  if (open) {
    if (!menuScrollLocked) {
      lockBodyScroll()
      menuScrollLocked = true
    }
    // 打开抽屉后把焦点移入（关闭按钮），避免焦点停留在被 inert 隐藏的页面主体
    await nextTick()
    drawerCloseButtonRef.value?.focus()
  } else if (menuScrollLocked) {
    unlockBodyScroll()
    menuScrollLocked = false
  }
  // 抽屉关闭时把焦点归还给汉堡按钮，保证键盘用户的 Tab 起点可预期
  if (!open && prev) {
    await nextTick()
    menuButtonRef.value?.focus()
  }
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  mobileQuery.addEventListener('change', onViewportChange)
  void loadSidebarVersion()
  // 2026-11-06: idle warmup 已停用（并发预载 8 个 chunk，任一 import 挂起会被
  // 路由复用同一 pending promise，导致导航确认后组件不挂载而空白）。
  // hover/focus/pointerdown 的单页预载保留，首次跳转改为按需加载。
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  mobileQuery.removeEventListener('change', onViewportChange)
  if (menuScrollLocked) {
    unlockBodyScroll()
    menuScrollLocked = false
  }
})

const viewLoaders: Record<string, () => Promise<unknown>> = {
  dashboard: () => import('../views/Dashboard.vue'),
  accounts: () => import('../views/Accounts.vue'),
  chats: () => import('../views/Chats.vue'),
  proxies: () => import('../views/Proxies.vue'),
  tasks: () => import('../views/Tasks.vue'),
  plugins: () => import('../views/Plugins.vue'),
  logs: () => import('../views/Logs.vue'),
  settings: () => import('../views/Settings.vue'),
}

const { prefetch: prefetchLoadedView } = createViewPrefetcher(viewLoaders, {
  warn: (name, error) => devLog.warn('页面预加载失败', { name, error }),
})

const prefetchView = (name: string) => {
  if (route.name === name) return
  prefetchLoadedView(name)
}

const navigation = [
  { id: 'dashboard', name: 'dashboard', icon: LayoutDashboard, labelKey: 'nav.dashboard' },
  { id: 'accounts', name: 'accounts', icon: Users, labelKey: 'nav.accounts' },
  { id: 'chats', name: 'chats', icon: MessageSquareText, labelKey: 'nav.chats' },
  { id: 'proxies', name: 'proxies', icon: Network, labelKey: 'nav.proxies' },
  { id: 'tasks', name: 'tasks', icon: Zap, labelKey: 'nav.tasks' },
  { id: 'plugins', name: 'plugins', icon: Puzzle, labelKey: 'nav.plugins' },
  { id: 'logs', name: 'logs', icon: Terminal, labelKey: 'nav.logs' },
  { id: 'settings', name: 'settings', icon: Settings, labelKey: 'nav.settings' },
]

const currentTitle = computed(() => {
  const current = navigation.find(n => n.name === route.name)
  if (!current) return 'TG-SignPulse'
  return t(current.labelKey)
})

// 浏览器标签页标题跟随当前页面，便于多标签切换时识别
watch(
  currentTitle,
  (title) => {
    document.title = title && title !== 'TG-SignPulse' ? `${title} - TG-SignPulse` : 'TG-SignPulse'
  },
  { immediate: true },
)

const openGithub = () => {
  window.open('https://github.com/Silentely/TG-SignPulse', '_blank')
}

const handleNavClick = () => {
  isMobileMenuOpen.value = false
}
</script>

<template>
  <div class="flex min-h-screen w-full overflow-x-hidden text-[var(--sp-text)] font-sans antialiased">
    <!-- 移动端遮罩：点击关闭侧栏 -->
    <div
      v-if="isMobileMenuOpen"
      class="fixed inset-0 bg-black/40 dark:bg-black/60 backdrop-blur-sm z-40 lg:hidden"
      aria-hidden="true"
      @click="isMobileMenuOpen = false"
    />

    <aside 
      class="ui-sidebar fixed inset-y-0 left-0 z-50 flex flex-col transition-transform duration-300 ease-in-out w-64"
      :class="isMobileMenuOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'"
      :aria-hidden="sidebarHidden ? 'true' : undefined"
      :inert="sidebarHidden || undefined"
    >
      <div class="flex items-center h-14 px-4 border-b border-[var(--sp-border)] gap-2.5">
        <div class="ui-brand-mark w-7 h-7 text-[11px] shrink-0 font-bold">TG</div>
        <div class="min-w-0 flex-1">
          <div class="font-mono font-semibold tracking-[0.18em] text-[var(--sp-text)] text-sm leading-none">SIGNPULSE</div>
          <div class="text-[10px] text-[var(--sp-text-muted)] mt-1 tracking-wide truncate">
            <button
              v-if="sidebarVersion"
              type="button"
              class="font-mono hover:text-[var(--sp-accent)] transition-colors"
              :title="t('settings.aboutTitle')"
              @click="goSettingsAbout"
            >{{ sidebarVersion }}</button>
            <span v-else>{{ t('common.brandSubtitle') }}</span>
          </div>
        </div>
        <!-- 仅移动端抽屉显示关闭；提高可点区域与层级 -->
        <button
          ref="drawerCloseButtonRef"
          type="button"
          class="lg:hidden shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-[var(--sp-radius)] text-[var(--sp-text-muted)] hover:text-[var(--sp-text)] hover:bg-black/[0.04] bg-[var(--sp-surface)]/[0.06] relative z-[60]"
          :aria-label="t('common.close')"
          @click.stop="isMobileMenuOpen = false"
        >
          <X class="w-4 h-4" stroke-width="2" />
        </button>
      </div>

      <nav class="flex-1 py-4 flex flex-col gap-1 px-3 overflow-y-auto custom-scrollbar" :aria-label="t('nav.mainNav')">
        <router-link 
          v-for="nav in navigation" 
          :key="nav.id"
          :to="{ name: nav.name }"
          class="flex items-center h-9 px-3 transition-colors whitespace-nowrap rounded-[var(--sp-radius)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--sp-accent)]"
          :class="route.name === nav.name
            ? 'ui-nav-active font-medium'
            : 'text-[var(--sp-text-secondary)] hover:text-[var(--sp-text)] hover:bg-black/[0.03] bg-[var(--sp-surface)]/[0.04]'"
          :aria-current="route.name === nav.name ? 'page' : undefined"
          @click="handleNavClick"
          @mouseenter="prefetchView(nav.name)"
          @focus="prefetchView(nav.name)"
          @pointerdown="prefetchView(nav.name)"
        >
          <component :is="nav.icon" class="w-[17px] h-[17px] shrink-0 opacity-80" stroke-width="1.75" />
          <span class="ml-3 text-xs sm:text-[13px] tracking-wide">{{ t(nav.labelKey) }}</span>
        </router-link>
      </nav>

      <div class="border-t border-[var(--sp-border)] p-3">
        <button
          type="button"
          data-testid="user-profile-btn"
          class="flex items-center w-full h-9 px-3 transition-colors whitespace-nowrap rounded-[var(--sp-radius)] text-[var(--sp-text-secondary)] hover:text-[var(--sp-text)] hover:bg-black/[0.03] bg-[var(--sp-surface)]/[0.04]"
          @click="showProfileModal = true; isMobileMenuOpen = false"
        >
          <UserCircle class="w-[17px] h-[17px] shrink-0 opacity-80" stroke-width="1.75" />
          <span class="ml-3 text-xs sm:text-[13px] tracking-wide font-medium">{{ t('nav.profile') }}</span>
        </button>
      </div>
    </aside>

    <main class="flex-1 w-full pl-0 lg:pl-64 flex flex-col min-h-screen transition-all duration-300 max-w-[100vw]">
      <header class="ui-header-glass sticky top-0 z-30 h-14 flex items-center justify-between px-4 sm:px-6 lg:px-8 shrink-0">
        <div class="flex items-center gap-3 min-w-0">
          <button
            ref="menuButtonRef"
            type="button"
            class="ui-icon-btn lg:hidden"
            :aria-label="t('nav.openMenu')"
            :aria-expanded="isMobileMenuOpen"
            @click="isMobileMenuOpen = true"
          >
            <Menu class="w-5 h-5" />
          </button>
          <h1 class="text-sm sm:text-base font-medium text-[var(--sp-text)] tracking-wide truncate">
            {{ currentTitle }}
          </h1>
        </div>
        <div class="flex items-center gap-1 sm:gap-1.5">
          <button
            type="button"
            class="ui-icon-btn"
            :title="t('common.github')"
            :aria-label="t('common.github')"
            @click="openGithub"
          >
            <Github class="w-4 h-4" />
          </button>
          <button
            type="button"
            class="ui-icon-btn"
            :title="locale === 'zh' ? t('language.switchToEn') : t('language.switchToZh')"
            :aria-label="t('common.changeLanguage')"
            @click="toggleLanguage"
          >
            <Globe class="w-4 h-4" />
          </button>
          <button
            type="button"
            class="ui-icon-btn"
            :title="isDark ? t('common.lightMode') : t('common.darkMode')"
            :aria-label="isDark ? t('common.lightMode') : t('common.darkMode')"
            @click="toggleTheme($event)"
          >
            <Moon v-if="!isDark" class="w-4 h-4" />
            <Sun v-else class="w-4 h-4" />
          </button>
        </div>
      </header>

      <!-- 避免 overflow-x-hidden 破坏子元素 sticky；用 clip 裁剪即可 -->
      <div class="flex-1 px-4 sm:px-6 lg:px-8 py-6 pb-14 overflow-x-clip">
        <div v-if="viewError" class="ui-card p-5 space-y-3">
          <p class="text-sm font-medium text-[var(--sp-text)]">{{ t('common.viewRenderFailed') }}</p>
          <p class="text-xs text-[var(--sp-text-muted)] break-words font-mono">{{ viewError }}</p>
          <div class="flex flex-wrap gap-2">
            <button type="button" class="ui-btn-primary !px-3 !py-2 !text-xs cursor-pointer" @click="retryView">{{ t('common.retry') }}</button>
            <button type="button" class="ui-btn-secondary !px-3 !py-2 !text-xs cursor-pointer" @click="reloadPage">{{ t('common.reloadPage') }}</button>
          </div>
        </div>
        <router-view v-else v-slot="{ Component }">
          <!-- 2026-11-06: 去掉 mode="out-in"。out-in 会等旧组件 leave 完成才挂载新组件，
               一旦 leave 卡住新页面就永远不挂载（空白、无报错、刷新才恢复）。
               默认并行模式新组件立即挂载，旧组件同时淡出，不再互相阻塞。
               @after-enter/@after-leave 只写 console 诊断，线上出问题能定位到哪一步。 -->
          <transition
            name="fade"
            @before-enter="() => console.info('[SignPulse:nav] enter', String(route.name))"
            @after-enter="() => console.info('[SignPulse:nav] entered', String(route.name))"
            @after-leave="() => console.info('[SignPulse:nav] left', String(route.name))"
          >
            <component :is="Component" :key="viewKey" />
          </transition>
        </router-view>
      </div>

      <UserProfileModal :isOpen="showProfileModal" @close="showProfileModal = false" />
    </main>
  </div>
</template>

<style>
::view-transition-old(root),
::view-transition-new(root) {
  animation: none;
  mix-blend-mode: normal;
}
</style>
<style scoped>
.fade-enter-active {
  transition: opacity 0.12s ease-out, transform 0.12s cubic-bezier(0.16, 1, 0.3, 1);
  will-change: opacity, transform;
}
.fade-leave-active {
  transition: opacity 0.08s ease-in, transform 0.08s ease-in;
}
.fade-enter-from {
  opacity: 0;
  transform: translateY(3px);
}
.fade-leave-to {
  opacity: 0;
  transform: translateY(-2px);
}
</style>
