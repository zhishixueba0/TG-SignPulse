<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  RefreshCw,
  Play,
  Gauge,
  Plus,
  Trash2,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  Network,
} from 'lucide-vue-next'
import { getAuthToken } from '../lib/api/core'
import {
  listProxies,
  createProxy,
  importProxies,
  batchDeleteProxies,
  deleteProxy,
  renameProxy,
  testProxy,
  getProxyLink,
  startTestAllProxies,
  getTestAllProxiesJob,
  startCleanupBlockedProxies,
  getCleanupBlockedProxiesJob,
  getProxySubscription,
  saveProxySubscription,
  startProxySync,
  getProxySyncJob,
  listProxyRoutes,
  updateProxyRoute,
  setGlobalProxy,
  testGlobalProxy,
  proxyOptionLabel,
  sortProxyOptions,
  type ProxyItem,
  type ProxyRoute,
  type ProxyMode,
  type ProxyType,
  type ProxyJobProgress,
  type ProxyCleanupJobProgress,
  type ProxySyncJobProgress,
  type ProxyJobStatus,
} from '../lib/api/proxies'
import { useI18n } from '../composables/useI18n'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import Modal from '../components/Modal.vue'
import PageRetry from '../components/PageRetry.vue'
import { notifyApiError } from '../lib/notify'
import { copyToClipboard } from '../lib/clipboard'
import { getLocalizedErrorMessage } from '../lib/types'
import { countryName, flagImageUrl } from '../lib/country-names'
import { devLog } from '../lib/devLog'

const { t } = useI18n()
const toast = useToast()
const { confirm } = useConfirm()

const proxies = ref<ProxyItem[]>([])
const total = ref(0)
const routes = ref<ProxyRoute[]>([])
const globalProxy = ref<{ set: boolean; display_url: string | null }>({ set: false, display_url: null })
const subscriptionUrl = ref('')
const defaultSubscriptionUrl = ref('')
const pageLoading = ref(true)
const loadError = ref(false)
const refreshing = ref(false)

/** 检测 / 清理 / 同步三类后台任务进度 */
const testAllJob = ref<ProxyJobProgress | null>(null)
const cleanupJob = ref<ProxyCleanupJobProgress | null>(null)
const syncJob = ref<ProxySyncJobProgress | null>(null)
const testAllRunning = computed(() => testAllJob.value?.status === 'running')
const cleanupRunning = computed(() => cleanupJob.value?.status === 'running')
const syncRunning = computed(() => syncJob.value?.status === 'running')
const poolEmpty = computed(() => proxies.value.length === 0)

/** 批量选择 */
const selectedIds = ref<Set<string>>(new Set())
const selectedCount = computed(() => selectedIds.value.size)
const allSelected = computed(
  () => proxies.value.length > 0 && proxies.value.every((p) => selectedIds.value.has(p.id)),
)
const toggleSelect = (id: string) => {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}
const toggleSelectAll = () => {
  selectedIds.value = allSelected.value
    ? new Set()
    : new Set(proxies.value.map((p) => p.id))
}
const toggleGroupSelect = (items: ProxyItem[]) => {
  const next = new Set(selectedIds.value)
  const allIn = items.every((p) => next.has(p.id))
  for (const p of items) {
    if (allIn) next.delete(p.id)
    else next.add(p.id)
  }
  selectedIds.value = next
}

/** 分组折叠状态（localStorage 持久化） */
const COLLAPSED_KEY = 'tg-signpulse-proxy-groups-collapsed'
const loadCollapsed = (): string[] => {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}
const collapsedGroups = ref<string[]>(loadCollapsed())
const isCollapsed = (key: string) => collapsedGroups.value.includes(key)
const toggleGroup = (key: string) => {
  const next = collapsedGroups.value.includes(key)
    ? collapsedGroups.value.filter((k) => k !== key)
    : [...collapsedGroups.value, key]
  collapsedGroups.value = next
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next))
  } catch {
    /* 忽略存储失败 */
  }
}

/** 按宣布归属地分组：>=5 条自成一组，其余并入「其他」；按数量倒序，其他在最后 */
interface ProxyGroup {
  key: string
  code: string | null
  label: string
  items: ProxyItem[]
}
/** 按解析 IP 筛选（点卡片上的“同 IP ×N”进入，筛选条上可清除） */
const ipFilter = ref<string | null>(null)
/** 多少个已加载代理解析到同一 IP（用于“同 IP ×N”标记） */
const sameIpCount = (ip: string | null | undefined): number => {
  if (!ip) return 0
  let n = 0
  for (const p of proxies.value) {
    if (p.resolved_ip === ip) n++
  }
  return n
}
const groups = computed<ProxyGroup[]>(() => {
  const list = ipFilter.value
    ? proxies.value.filter((p) => p.resolved_ip === ipFilter.value)
    : proxies.value
  const byCode = new Map<string, ProxyItem[]>()
  for (const p of list) {
    const cc = (p.country_code || '').toUpperCase()
    const key = cc || 'UNKNOWN'
    const list = byCode.get(key) ?? []
    list.push(p)
    byCode.set(key, list)
  }
  const big: ProxyGroup[] = []
  const small: ProxyItem[] = []
  for (const [key, items] of byCode) {
    if (items.length >= 5) {
      big.push({
        key,
        code: key === 'UNKNOWN' ? null : key,
        label: key === 'UNKNOWN' ? t('proxies.unknownRegion') : countryName(key),
        items,
      })
    } else {
      small.push(...items)
    }
  }
  big.sort((a, b) => b.items.length - a.items.length)
  if (small.length > 0) {
    big.push({ key: 'OTHER', code: null, label: t('proxies.other'), items: small })
  }
  return big
})

/** 路由选代理下拉：按名称字母排序（去旗帜后比较） */
const sortedProxies = computed(() => sortProxyOptions(proxies.value))

const formatTime = (s: string | null | undefined): string =>
  s ? s.slice(0, 16).replace('T', ' ') : ''

const loadProxies = async () => {
  const token = getAuthToken()
  if (!token) return
  const res = await listProxies(token)
  proxies.value = res.proxies
  total.value = res.total
}

const loadRoutes = async () => {
  const token = getAuthToken()
  if (!token) return
  const res = await listProxyRoutes(token)
  routes.value = res.routes
  globalProxy.value = res.global_proxy
}

const loadSubscription = async () => {
  const token = getAuthToken()
  if (!token) return
  const res = await getProxySubscription(token)
  subscriptionUrl.value = res.url ?? ''
  defaultSubscriptionUrl.value = res.default_url
}

const loadAll = async () => {
  const token = getAuthToken()
  if (!token) return
  try {
    loadError.value = false
    await Promise.all([loadProxies(), loadRoutes(), loadSubscription()])
    // 刷新页面后恢复进行中的后台任务轮询
    await resumeRunningJobs()
  } catch (e: unknown) {
    devLog.error('Failed to load proxies page', e)
    loadError.value = true
    notifyApiError(e, 'proxies.loadFailed')
  } finally {
    pageLoading.value = false
  }
}

const handleRefresh = async () => {
  refreshing.value = true
  try {
    await Promise.all([loadProxies(), loadRoutes(), loadSubscription()])
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.loadFailed')
  } finally {
    refreshing.value = false
  }
}

/** ─── 后台任务轮询 ─── */
interface PollSlot {
  timer: ReturnType<typeof setInterval> | null
}
const testAllPoll: PollSlot = { timer: null }
const cleanupPoll: PollSlot = { timer: null }
const syncPoll: PollSlot = { timer: null }

const stopPolling = (slot: PollSlot) => {
  if (slot.timer) {
    clearInterval(slot.timer)
    slot.timer = null
  }
}

function startPolling<T extends { status: ProxyJobStatus }>(
  slot: PollSlot,
  fetcher: (token: string) => Promise<T>,
  apply: (job: T) => void,
  onFinish: () => void,
) {
  stopPolling(slot)
  slot.timer = setInterval(async () => {
    const token = getAuthToken()
    if (!token) {
      stopPolling(slot)
      return
    }
    try {
      const job = await fetcher(token)
      apply(job)
      if (job.status === 'done' || job.status === 'error') {
        stopPolling(slot)
        onFinish()
      }
    } catch (e: unknown) {
      devLog.error('代理任务轮询失败', e)
      stopPolling(slot)
    }
  }, 1500)
}

const resumeRunningJobs = async () => {
  const token = getAuthToken()
  if (!token) return
  try {
    const [testJob, cleanJob, sync] = await Promise.all([
      getTestAllProxiesJob(token),
      getCleanupBlockedProxiesJob(token),
      getProxySyncJob(token),
    ])
    if (testJob.status === 'running') {
      testAllJob.value = testJob
      startPolling(testAllPoll, getTestAllProxiesJob, (j) => { testAllJob.value = j }, () => { void loadProxies() })
    } else if (testJob.status !== 'idle') {
      testAllJob.value = testJob
    }
    if (cleanJob.status === 'running') {
      cleanupJob.value = cleanJob
      startPolling(cleanupPoll, getCleanupBlockedProxiesJob, (j) => { cleanupJob.value = j }, () => { void loadProxies(); void loadRoutes() })
    } else if (cleanJob.status !== 'idle') {
      cleanupJob.value = cleanJob
    }
    if (sync.status === 'running') {
      syncJob.value = sync
      startPolling(syncPoll, getProxySyncJob, (j) => { syncJob.value = j }, () => { void loadProxies() })
    } else if (sync.status !== 'idle') {
      syncJob.value = sync
    }
  } catch (e: unknown) {
    devLog.error('恢复代理任务状态失败', e)
  }
}

onMounted(() => {
  void loadAll()
})

onUnmounted(() => {
  stopPolling(testAllPoll)
  stopPolling(cleanupPoll)
  stopPolling(syncPoll)
})

/** ─── 单个代理：测 / 改名 / 删 / 复制链接 ─── */
const testingId = ref('')
const copyingLinkId = ref('')

/** 卡片“复制链接”：向后端按需取单条节点链接（MTProto 为 tg://proxy 链接）再复制。 */
const handleCopyProxyLink = async (p: ProxyItem) => {
  const token = getAuthToken()
  if (!token || copyingLinkId.value) return
  copyingLinkId.value = p.id
  try {
    const res = await getProxyLink(token, p.id)
    if (await copyToClipboard(res.link)) toast.success(t('common.copied'))
    else toast.error(t('proxies.copyLinkFailed'))
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.copyLinkFailed')
  } finally {
    copyingLinkId.value = ''
  }
}

const handleTestProxy = async (p: ProxyItem) => {
  const token = getAuthToken()
  if (!token || testingId.value) return
  testingId.value = p.id
  try {
    const res = await testProxy(token, p.id)
    p.latency_ms = res.latency_ms
    p.last_test_ok = res.last_test_ok
    p.last_tested_at = new Date().toISOString()
    // TCP 维度：端口通但握手失败时，明确说明端口是通的（不是线路全断），
    // 结论仍是不可用——绝不把 verdict 降级成“可用”。
    const tcpNote = !res.ok && res.tcp_ok ? `（${t('proxies.tcpPortOpen')}）` : ''
    if (res.ok) toast.success(res.detail || t('proxies.testOk'))
    else toast.error((res.detail || t('proxies.testFail')) + tcpNote)
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.testFailed')
  } finally {
    testingId.value = ''
  }
}

const renameTarget = ref<ProxyItem | null>(null)
const renameLabel = ref('')
const renameSaving = ref(false)
const openRename = (p: ProxyItem) => {
  renameTarget.value = p
  renameLabel.value = p.label
}
const saveRename = async () => {
  const token = getAuthToken()
  const target = renameTarget.value
  const label = renameLabel.value.trim()
  if (!token || !target) return
  renameSaving.value = true
  try {
    const res = await renameProxy(token, target.id, label)
    target.label = res.proxy.label
    toast.success(t('proxies.renameSuccess'))
    renameTarget.value = null
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.renameFailed')
  } finally {
    renameSaving.value = false
  }
}

const handleDeleteProxy = async (p: ProxyItem) => {
  const ok = await confirm({
    title: t('proxies.deleteConfirmTitle'),
    message: t('proxies.deleteConfirm'),
    confirmText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  const token = getAuthToken()
  if (!token) return
  try {
    await deleteProxy(token, p.id)
    toast.success(t('proxies.deleteSuccess'))
    selectedIds.value = new Set([...selectedIds.value].filter((id) => id !== p.id))
    await Promise.all([loadProxies(), loadRoutes()])
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.deleteFailed')
  }
}

const batchDeleting = ref(false)
const handleBatchDelete = async () => {
  if (selectedCount.value === 0) {
    toast.warning(t('proxies.noSelection'))
    return
  }
  const ok = await confirm({
    title: t('proxies.batchDeleteConfirmTitle'),
    message: t('proxies.batchDeleteConfirm', { count: selectedCount.value }),
    confirmText: t('common.delete'),
    danger: true,
  })
  if (!ok) return
  const token = getAuthToken()
  if (!token) return
  batchDeleting.value = true
  try {
    await batchDeleteProxies(token, [...selectedIds.value])
    toast.success(t('proxies.deleteSuccess'))
    selectedIds.value = new Set()
    await Promise.all([loadProxies(), loadRoutes()])
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.deleteFailed')
  } finally {
    batchDeleting.value = false
  }
}

/** ─── 检测全部 / 测速 ─── */
const startTestAll = async (mode: 'probe' | 'tcp') => {
  if (poolEmpty.value) return
  const token = getAuthToken()
  if (!token) return
  try {
    const res = await startTestAllProxies(token, mode)
    if (!res.started) return
    testAllJob.value = { status: 'running', total: proxies.value.length, done: 0, ok: 0, fail: 0 }
    toast.info(t('proxies.testAllStarted'))
    startPolling(
      testAllPoll,
      getTestAllProxiesJob,
      (job) => { testAllJob.value = job },
      () => {
        const job = testAllJob.value
        if (job?.status === 'done') {
          toast.success(t('proxies.testAllDone', { ok: job.ok, fail: job.fail }))
        } else if (job?.status === 'error') {
          toast.error(t('proxies.jobError', { error: job.error || '' }))
        }
        void loadProxies()
      },
    )
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.testAllFailed')
  }
}

/** ─── 一键清理中伊代理 ─── */
const handleCleanup = async () => {
  const ok = await confirm({
    title: t('proxies.cleanupConfirmTitle'),
    message: t('proxies.cleanupConfirm'),
    confirmText: t('proxies.cleanup'),
    danger: true,
  })
  if (!ok) return
  const token = getAuthToken()
  if (!token) return
  try {
    const res = await startCleanupBlockedProxies(token)
    if (!res.started) return
    cleanupJob.value = { status: 'running', total: 0, done: 0, removed: 0 }
    toast.info(t('proxies.cleanupStarted'))
    startPolling(
      cleanupPoll,
      getCleanupBlockedProxiesJob,
      (job) => { cleanupJob.value = job },
      () => {
        const job = cleanupJob.value
        if (job?.status === 'done') {
          toast.success(t('proxies.cleanupDone', { count: job.removed }))
        } else if (job?.status === 'error') {
          toast.error(t('proxies.jobError', { error: job.error || '' }))
        }
        void loadProxies()
        void loadRoutes()
      },
    )
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.cleanupFailed')
  }
}

/** ─── 添加代理 ─── */
const showAddModal = ref(false)
const addTab = ref<'paste' | 'manual'>('paste')
const importText = ref('')
const importing = ref(false)
const recognizedCount = computed(
  () => importText.value.split('\n').map((s) => s.trim()).filter(Boolean).length,
)
const manualForm = ref({
  ptype: 'socks5' as ProxyType,
  host: '',
  port: '',
  username: '',
  password: '',
  secret: '',
  label: '',
  region: '',
})
const creating = ref(false)

const resetAddModal = () => {
  addTab.value = 'paste'
  importText.value = ''
  manualForm.value = {
    ptype: 'socks5',
    host: '',
    port: '',
    username: '',
    password: '',
    secret: '',
    label: '',
    region: '',
  }
}

const handleImport = async () => {
  const token = getAuthToken()
  if (!token || recognizedCount.value === 0) return
  importing.value = true
  try {
    const res = await importProxies(token, importText.value)
    toast.success(t('proxies.importResult', { added: res.added, skipped: res.skipped, blocked: res.blocked }))
    showAddModal.value = false
    resetAddModal()
    await loadProxies()
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.importFailed')
  } finally {
    importing.value = false
  }
}

const handleCreateProxy = async () => {
  const token = getAuthToken()
  if (!token) return
  const f = manualForm.value
  const host = f.host.trim()
  const port = Number(f.port)
  if (!host || !Number.isInteger(port) || port <= 0 || port > 65535) {
    toast.error(t('proxies.createFailed'))
    return
  }
  if (f.ptype === 'mtproto' && !f.secret.trim()) {
    toast.error(t('proxies.createFailed'))
    return
  }
  creating.value = true
  try {
    const res = await createProxy(token, {
      ptype: f.ptype,
      host,
      port,
      username: f.username.trim() || undefined,
      password: f.password || undefined,
      secret: f.ptype === 'mtproto' ? f.secret.trim() : undefined,
      label: f.label.trim() || undefined,
      auto_name: !f.label.trim(),
      region: f.region.trim().toUpperCase() || null,
    })
    if (res.blocked) {
      toast.warning(res.message || t('proxies.blockedProxy'))
    } else {
      toast.success(res.message || t('proxies.createSuccess'))
    }
    showAddModal.value = false
    resetAddModal()
    await loadProxies()
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.createFailed')
  } finally {
    creating.value = false
  }
}

/** ─── 订阅同步 ─── */
const subscriptionSaving = ref(false)
const saveSubscription = async () => {
  const token = getAuthToken()
  if (!token) return
  subscriptionSaving.value = true
  try {
    const res = await saveProxySubscription(token, subscriptionUrl.value.trim() || null)
    subscriptionUrl.value = res.url ?? ''
    toast.success(t('proxies.subscriptionSaved'))
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.subscriptionSaveFailed')
  } finally {
    subscriptionSaving.value = false
  }
}

const handleSync = async () => {
  const token = getAuthToken()
  if (!token) return
  try {
    const res = await startProxySync(token)
    if (res.already_running) {
      toast.warning(t('proxies.syncAlreadyRunning'))
      return
    }
    if (!res.started) return
    syncJob.value = {
      status: 'running',
      fetched: 0,
      tested: 0,
      added: 0,
      kept: 0,
      removed: 0,
      blocked_removed: 0,
      telegram_failed: 0,
      started_at: null,
      finished_at: null,
    }
    toast.info(t('proxies.syncStarted'))
    startPolling(
      syncPoll,
      getProxySyncJob,
      (job) => { syncJob.value = job },
      () => {
        const job = syncJob.value
        if (job?.status === 'done') {
          toast.success(t('proxies.syncDone', { added: job.added, kept: job.kept, removed: job.removed }))
        } else if (job?.status === 'error') {
          toast.error(t('proxies.jobError', { error: job.error || '' }))
        }
        void loadProxies()
      },
    )
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.syncFailed')
  }
}

/** ─── 全局代理 ─── */
const globalProxyInput = ref('')
const globalSaving = ref(false)
const globalTesting = ref(false)

const saveGlobalProxy = async () => {
  const token = getAuthToken()
  const url = globalProxyInput.value.trim()
  if (!token || !url) return
  globalSaving.value = true
  try {
    await setGlobalProxy(token, url)
    globalProxyInput.value = ''
    toast.success(t('proxies.globalSet'))
    await loadRoutes()
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.loadFailed')
  } finally {
    globalSaving.value = false
  }
}

const clearGlobalProxy = async () => {
  const token = getAuthToken()
  if (!token) return
  globalSaving.value = true
  try {
    await setGlobalProxy(token, null)
    toast.success(t('proxies.globalCleared'))
    await loadRoutes()
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.loadFailed')
  } finally {
    globalSaving.value = false
  }
}

const testGlobalProxyNow = async () => {
  const token = getAuthToken()
  if (!token) return
  globalTesting.value = true
  try {
    const res = await testGlobalProxy(token)
    const tcpNote = !res.ok && res.tcp_ok ? `（${t('proxies.tcpPortOpen')}）` : ''
    if (res.ok) toast.success(res.detail || t('proxies.globalTestOk'))
    else toast.error((res.detail || t('proxies.globalTestFail')) + tcpNote)
  } catch (e: unknown) {
    notifyApiError(e, 'proxies.globalTestFailed')
  } finally {
    globalTesting.value = false
  }
}

/** ─── 账号路由 ─── */
const routeTarget = ref<ProxyRoute | null>(null)
const routeMode = ref<ProxyMode>('inherit')
const routeProxyId = ref('')
const routeError = ref('')
const routeSaving = ref(false)

const openRouteModal = (route: ProxyRoute) => {
  routeTarget.value = route
  // 路由弹窗只提供 inherit / direct / pool 三档；custom（编辑页自定义线路）打开时暂按继承展示
  routeMode.value = route.mode === 'inherit' || route.mode === 'direct' || route.mode === 'pool'
    ? route.mode
    : 'inherit'
  routeProxyId.value = route.proxy_id ?? ''
  routeError.value = ''
}

const saveRoute = async () => {
  const token = getAuthToken()
  const target = routeTarget.value
  if (!token || !target) return
  routeSaving.value = true
  routeError.value = ''
  try {
    await updateProxyRoute(token, {
      account_name: target.account_name,
      mode: routeMode.value,
      proxy_id: routeMode.value === 'pool' ? (routeProxyId.value || null) : null,
    })
    toast.success(t('proxies.routeUpdated'))
    routeTarget.value = null
    await loadRoutes()
  } catch (e: unknown) {
    // 账号在线时后端返回 409：原样展示后端文案
    routeError.value = getLocalizedErrorMessage(e, t, t('proxies.routeUpdateFailed'))
  } finally {
    routeSaving.value = false
  }
}
</script>

<template>
  <div class="space-y-4 pb-20">
    <!-- 页头 -->
    <div class="flex flex-col sm:flex-row sm:items-center gap-3">
      <div class="flex-1 min-w-0">
        <h2 class="text-base font-medium text-[var(--sp-text)] flex items-center gap-2">
          <Network class="w-4 h-4 text-[var(--sp-text-muted)]" />
          {{ t('proxies.title') }}
        </h2>
        <p class="text-xs text-[var(--sp-text-muted)] mt-1">{{ t('proxies.subtitle') }}</p>
      </div>
      <div class="flex items-center gap-2 shrink-0 flex-wrap">
        <button
          type="button"
          class="ui-btn-secondary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
          :disabled="refreshing"
          @click="handleRefresh"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="refreshing ? 'animate-spin' : ''" />
          {{ t('common.refresh') }}
        </button>
        <button
          type="button"
          class="ui-btn-secondary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
          :disabled="poolEmpty || testAllRunning"
          :title="poolEmpty ? t('proxies.testAllEmptyHint') : undefined"
          @click="startTestAll('probe')"
        >
          <Play class="w-3.5 h-3.5" />
          {{ t('proxies.testAll') }}
        </button>
        <button
          type="button"
          class="ui-btn-secondary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
          :disabled="poolEmpty || testAllRunning"
          :title="poolEmpty ? t('proxies.testAllEmptyHint') : undefined"
          @click="startTestAll('tcp')"
        >
          <Gauge class="w-3.5 h-3.5" />
          {{ t('proxies.speedTest') }}
        </button>
        <button
          type="button"
          class="ui-btn-primary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
          @click="showAddModal = true"
        >
          <Plus class="w-3.5 h-3.5" />
          {{ t('proxies.addProxy') }}
        </button>
      </div>
    </div>

    <div v-if="pageLoading" class="space-y-4" aria-busy="true">
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
        <div v-for="i in 3" :key="i" class="ui-card p-5 space-y-3">
          <div class="ui-skeleton h-3 w-20" />
          <div class="ui-skeleton h-7 w-16" />
        </div>
      </div>
      <div class="ui-card p-5 space-y-3">
        <div class="ui-skeleton h-4 w-32" />
        <div class="ui-skeleton h-10 w-full" />
      </div>
    </div>

    <div v-else-if="loadError" class="space-y-4">
      <PageRetry
        :message="t('proxies.loadFailed')"
        :loading="pageLoading"
        @retry="pageLoading = true; loadAll()"
      />
    </div>

    <template v-else>
      <!-- 统计卡片 -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
        <div class="ui-card p-5 flex flex-col justify-between min-h-[96px]">
          <span class="ui-section-label">{{ t('proxies.totalProxies') }}</span>
          <span class="text-2xl sm:text-3xl font-mono font-semibold text-[var(--sp-text)] mt-3 tracking-tight">{{ total }}</span>
        </div>
        <div class="ui-card p-5 flex flex-col justify-between min-h-[96px]">
          <span class="ui-section-label">{{ t('proxies.accountRoutes') }}</span>
          <span class="text-2xl sm:text-3xl font-mono font-semibold text-[var(--sp-text)] mt-3 tracking-tight">{{ t('proxies.accountsCount', { count: routes.length }) }}</span>
        </div>
        <div class="ui-card p-5 flex flex-col justify-between min-h-[96px]">
          <span class="ui-section-label">{{ t('proxies.globalProxy') }}</span>
          <span
            class="text-base sm:text-lg font-mono font-semibold text-[var(--sp-text)] mt-3 tracking-tight truncate"
            :title="globalProxy.display_url || t('proxies.notSet')"
          >{{ globalProxy.set && globalProxy.display_url ? globalProxy.display_url : t('proxies.notSet') }}</span>
        </div>
      </div>

      <!-- 检测 / 清理任务进度 -->
      <div v-if="testAllJob && testAllJob.status !== 'idle'" class="ui-card p-4 space-y-2">
        <div class="flex items-center justify-between gap-2 text-xs">
          <span class="text-[var(--sp-text-secondary)]">{{ t('proxies.testAll') }}</span>
          <span class="font-mono text-[var(--sp-text-muted)]">
            <template v-if="testAllJob.status === 'running'">
              {{ t('proxies.jobRunning', { done: testAllJob.done, total: testAllJob.total }) }}
            </template>
            <template v-else>
              {{ testAllJob.ok }} {{ t('proxies.reachable') }} / {{ testAllJob.fail }} {{ t('proxies.unreachable') }}
            </template>
          </span>
        </div>
        <div class="w-full h-1.5 rounded-full bg-black/[0.06] dark:bg-white/10 overflow-hidden" role="progressbar" :aria-valuenow="testAllJob.done" aria-valuemin="0" :aria-valuemax="testAllJob.total || 1">
          <div class="h-full bg-sky-500 transition-all duration-300" :style="{ width: `${testAllJob.total ? Math.round((testAllJob.done / testAllJob.total) * 100) : 0}%` }" />
        </div>
        <p v-if="testAllJob.status === 'error'" class="text-xs text-rose-600 dark:text-rose-400">{{ t('proxies.jobError', { error: testAllJob.error || '' }) }}</p>
      </div>

      <div v-if="cleanupJob && cleanupJob.status !== 'idle'" class="ui-card p-4 space-y-2">
        <div class="flex items-center justify-between gap-2 text-xs">
          <span class="text-[var(--sp-text-secondary)]">{{ t('proxies.cleanup') }}</span>
          <span class="font-mono text-[var(--sp-text-muted)]">
            <template v-if="cleanupJob.status === 'running'">
              {{ t('proxies.jobRunning', { done: cleanupJob.done, total: cleanupJob.total }) }}
            </template>
            <template v-else>
              {{ t('proxies.cleanupDone', { count: cleanupJob.removed }) }}
            </template>
          </span>
        </div>
        <div class="w-full h-1.5 rounded-full bg-black/[0.06] dark:bg-white/10 overflow-hidden" role="progressbar" :aria-valuenow="cleanupJob.done" aria-valuemin="0" :aria-valuemax="cleanupJob.total || 1">
          <div class="h-full bg-rose-500 transition-all duration-300" :style="{ width: `${cleanupJob.total ? Math.round((cleanupJob.done / cleanupJob.total) * 100) : 0}%` }" />
        </div>
        <p v-if="cleanupJob.status === 'error'" class="text-xs text-rose-600 dark:text-rose-400">{{ t('proxies.jobError', { error: cleanupJob.error || '' }) }}</p>
      </div>

      <!-- 路由策略 -->
      <section class="ui-card p-5 space-y-4">
        <div>
          <h3 class="text-sm font-medium text-[var(--sp-text)]">{{ t('proxies.routePolicy') }}</h3>
          <p class="text-xs text-[var(--sp-text-muted)] mt-1">{{ t('proxies.routePolicyHint') }}</p>
        </div>
        <div class="flex flex-col sm:flex-row gap-2">
          <input
            v-model="globalProxyInput"
            type="text"
            class="ui-input flex-1 min-w-0 font-mono !text-xs"
            :placeholder="globalProxy.set && globalProxy.display_url ? globalProxy.display_url : t('proxies.globalProxyUrlPlaceholder')"
          >
          <div class="flex gap-2 shrink-0">
            <button
              type="button"
              class="ui-btn-primary !px-3 !py-2 !text-xs cursor-pointer"
              :disabled="globalSaving || !globalProxyInput.trim()"
              @click="saveGlobalProxy"
            >
              {{ t('proxies.setGlobal') }}
            </button>
            <button
              type="button"
              class="ui-btn-secondary !px-3 !py-2 !text-xs cursor-pointer"
              :disabled="globalSaving || !globalProxy.set"
              @click="clearGlobalProxy"
            >
              {{ t('proxies.clearGlobal') }}
            </button>
            <button
              type="button"
              class="ui-btn-secondary !px-3 !py-2 !text-xs cursor-pointer"
              :disabled="globalTesting || !globalProxy.set"
              @click="testGlobalProxyNow"
            >
              {{ t('proxies.testGlobal') }}
            </button>
          </div>
        </div>
        <div v-if="globalProxy.set" class="text-xs text-[var(--sp-text-muted)] truncate">
          {{ t('proxies.globalProxy') }}：<span class="font-mono text-[var(--sp-text-secondary)]" :title="globalProxy.display_url || ''">{{ globalProxy.display_url }}</span>
        </div>
      </section>

      <!-- 账号路由 -->
      <section class="ui-card p-5 space-y-3">
        <h3 class="text-sm font-medium text-[var(--sp-text)]">{{ t('proxies.accountRoutesTitle') }}</h3>
        <p v-if="routes.length === 0" class="text-xs text-[var(--sp-text-muted)]">{{ t('proxies.noRoutes') }}</p>
        <ul v-else class="divide-y divide-[var(--sp-border)]">
          <li v-for="route in routes" :key="route.account_name" class="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
            <div class="w-8 h-8 shrink-0 bg-[var(--sp-surface-muted)] rounded-[var(--sp-radius)] flex items-center justify-center text-xs text-[var(--sp-text-muted)] font-mono border border-[var(--sp-border)]">
              {{ route.account_name.substring(0, 2) }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-[var(--sp-text)] truncate">{{ route.account_name }}</div>
              <div class="text-xs text-[var(--sp-text-muted)] mt-0.5 font-mono truncate">
                {{ t('accounts.idNumber') }}：{{ route.account_name }}
                <span v-if="route.online" class="text-emerald-600 dark:text-emerald-400">· {{ t('accounts.statusOk') }}</span>
              </div>
            </div>
            <span class="text-xs text-[var(--sp-text-secondary)] truncate max-w-[40%] hidden sm:inline" :title="route.effective_label">{{ route.effective_label }}</span>
            <button
              type="button"
              class="ui-btn-secondary !px-3 !py-1.5 !text-xs shrink-0 cursor-pointer"
              @click="openRouteModal(route)"
            >
              {{ t('proxies.changeRoute') }}
            </button>
          </li>
        </ul>
      </section>

      <!-- 订阅同步 -->
      <section class="ui-card p-5 space-y-4">
        <h3 class="text-sm font-medium text-[var(--sp-text)]">{{ t('proxies.subscription') }}</h3>
        <div class="flex flex-col sm:flex-row gap-2">
          <input
            v-model="subscriptionUrl"
            type="text"
            class="ui-input flex-1 min-w-0 font-mono !text-xs"
            :placeholder="defaultSubscriptionUrl"
            :aria-label="t('proxies.subscriptionUrlLabel')"
          >
          <div class="flex gap-2 shrink-0">
            <button
              type="button"
              class="ui-btn-secondary !px-3 !py-2 !text-xs cursor-pointer"
              :disabled="subscriptionSaving"
              @click="saveSubscription"
            >
              {{ t('proxies.subscriptionSave') }}
            </button>
            <button
              type="button"
              class="ui-btn-primary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
              :disabled="syncRunning"
              @click="handleSync"
            >
              <span v-if="syncRunning" class="ui-spinner !w-3.5 !h-3.5 !border-2" />
              <RefreshCw v-else class="w-3.5 h-3.5" />
              {{ t('proxies.syncNow') }}
            </button>
          </div>
        </div>
        <div v-if="syncJob && syncJob.status !== 'idle'" class="text-xs text-[var(--sp-text-muted)] space-y-1">
          <p v-if="syncJob.status === 'running'">
            {{ t('proxies.jobRunning', { done: syncJob.tested, total: syncJob.fetched }) }}
            · {{ t('proxies.syncDone', { added: syncJob.added, kept: syncJob.kept, removed: syncJob.removed, telegram_failed: syncJob.telegram_failed ?? 0 }) }}
          </p>
          <p v-else-if="syncJob.status === 'done'">
            {{ t('proxies.syncDone', { added: syncJob.added, kept: syncJob.kept, removed: syncJob.removed, telegram_failed: syncJob.telegram_failed ?? 0 }) }}
          </p>
          <p v-else-if="syncJob.status === 'error'" class="text-rose-600 dark:text-rose-400">
            {{ t('proxies.jobError', { error: syncJob.error || '' }) }}
          </p>
        </div>
      </section>

      <!-- 代理列表 -->
      <section class="space-y-3">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="text-sm font-medium text-[var(--sp-text)] mr-auto">{{ t('proxies.proxyList') }}</h3>
          <label class="inline-flex items-center gap-1.5 text-xs text-[var(--sp-text-secondary)] cursor-pointer">
            <input type="checkbox" class="!w-auto" :checked="allSelected" :disabled="poolEmpty" @change="toggleSelectAll">
            {{ t('proxies.selectAll') }}
          </label>
          <span v-if="selectedCount > 0" class="text-xs text-[var(--sp-text-muted)]">{{ t('proxies.selectedCount', { count: selectedCount }) }}</span>
          <button
            type="button"
            class="ui-btn-danger !px-3 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer"
            :disabled="selectedCount === 0 || batchDeleting"
            @click="handleBatchDelete"
          >
            <Trash2 class="w-3.5 h-3.5" />
            {{ t('proxies.batchDelete') }}
          </button>
          <button
            type="button"
            class="ui-btn-secondary !px-3 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer"
            :disabled="poolEmpty || cleanupRunning"
            @click="handleCleanup"
          >
            <ShieldAlert class="w-3.5 h-3.5" />
            {{ t('proxies.cleanup') }}
          </button>
        </div>

        <div v-if="proxies.length === 0" class="ui-empty !py-12">
          <p class="ui-empty-desc">{{ t('proxies.noProxies') }}</p>
        </div>

        <div v-if="ipFilter" class="flex items-center gap-2 rounded-[var(--sp-radius)] border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs">
          <span class="font-mono text-amber-700 dark:text-amber-300">{{ t('proxies.ipFilterActive', { ip: ipFilter }) }}</span>
          <button type="button" class="ml-auto shrink-0 underline cursor-pointer text-amber-700 dark:text-amber-300" @click="ipFilter = null">
            {{ t('proxies.clearFilter') }}
          </button>
        </div>

        <div v-for="group in groups" :key="group.key" class="ui-card overflow-hidden">
          <button
            type="button"
            class="w-full flex items-center gap-2 px-4 py-3 text-left cursor-pointer hover:bg-black/[0.02] dark:hover:bg-white/[0.03]"
            @click="toggleGroup(group.key)"
          >
            <ChevronDown v-if="!isCollapsed(group.key)" class="w-4 h-4 text-[var(--sp-text-muted)] shrink-0" />
            <ChevronRight v-else class="w-4 h-4 text-[var(--sp-text-muted)] shrink-0" />
            <img
              v-if="group.code"
              :src="flagImageUrl(group.code)"
              :alt="group.code"
              class="w-5 h-auto rounded-[2px] shrink-0"
              loading="lazy"
            >
            <span class="text-sm font-medium text-[var(--sp-text)]">{{ group.label }}</span>
            <span v-if="group.code" class="font-mono text-xs text-[var(--sp-text-muted)]">{{ group.code }}</span>
            <span class="text-xs text-[var(--sp-text-muted)]">({{ group.items.length }})</span>
            <span class="flex-1" />
            <label
              class="inline-flex items-center gap-1.5 text-xs text-[var(--sp-text-secondary)] cursor-pointer"
              @click.stop
            >
              <input
                type="checkbox"
                class="!w-auto"
                :checked="group.items.every((p) => selectedIds.has(p.id))"
                @change="toggleGroupSelect(group.items)"
              >
              {{ t('proxies.selectAll') }}
            </label>
          </button>

          <div v-if="!isCollapsed(group.key)" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-3 pt-0">
            <div
              v-for="p in group.items"
              :key="p.id"
              class="rounded-[var(--sp-radius)] border border-[var(--sp-border)] bg-[var(--sp-surface)] p-4 space-y-2.5"
            >
              <div class="flex items-start gap-2">
                <input
                  type="checkbox"
                  class="!w-auto mt-0.5 shrink-0"
                  :checked="selectedIds.has(p.id)"
                  :aria-label="p.label"
                  @change="toggleSelect(p.id)"
                >
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium text-[var(--sp-text)] truncate" :title="p.label">{{ p.label }}</div>
                  <div class="text-xs text-[var(--sp-text-muted)] font-mono break-all mt-0.5" :title="p.display_url">{{ p.display_url }}</div>
                  <div v-if="p.resolved_ip" class="flex items-center gap-1.5 text-xs mt-1">
                    <span class="font-mono text-[var(--sp-text-secondary)]" :title="p.resolved_at ? t('proxies.resolvedAt', { time: formatTime(p.resolved_at) }) : ''">IP {{ p.resolved_ip }}</span>
                    <button
                      v-if="sameIpCount(p.resolved_ip) > 1"
                      type="button"
                      class="shrink-0 rounded-full border border-amber-500/50 bg-amber-500/10 px-1.5 py-px text-[11px] text-amber-700 dark:text-amber-300 cursor-pointer"
                      :title="t('proxies.sameIpFilterHint')"
                      @click="ipFilter = p.resolved_ip"
                    >
                      {{ t('proxies.sameIp', { count: sameIpCount(p.resolved_ip) }) }}
                    </button>
                  </div>
                </div>
                <span class="ui-badge shrink-0 font-mono uppercase">{{ p.ptype }}</span>
              </div>

              <div class="flex items-center gap-1.5 text-xs text-[var(--sp-text-secondary)]">
                <img
                  v-if="p.country_code"
                  :src="flagImageUrl(p.country_code)"
                  :alt="p.country_code"
                  class="w-5 h-auto rounded-[2px]"
                  loading="lazy"
                >
                <span>{{ p.country_code ? countryName(p.country_code) : t('proxies.unknownRegion') }}</span>
                <span v-if="p.country_code" class="font-mono text-[var(--sp-text-muted)]">{{ p.country_code.toUpperCase() }}</span>
              </div>

              <div class="text-xs text-[var(--sp-text-muted)]">
                {{ p.country_code ? countryName(p.country_code) : t('proxies.unknownRegion') }}
                ·
                {{ p.is_hosting === true ? t('proxies.hosting') : p.is_hosting === false ? t('proxies.isp') : t('proxies.unknown') }}
                <template v-if="p.org || p.isp">· {{ p.org || p.isp }}</template>
              </div>

              <div class="flex items-center gap-2 text-xs">
                <span class="font-mono text-[var(--sp-text-secondary)]">
                  {{ p.latency_ms !== null && p.latency_ms !== undefined ? t('proxies.latencyMs', { ms: p.latency_ms }) : '—' }}
                </span>
                <span
                  :class="p.last_test_ok === true
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : p.last_test_ok === false
                      ? 'text-rose-600 dark:text-rose-400'
                      : 'text-[var(--sp-text-muted)]'"
                >
                  {{ p.last_test_ok === true ? `●${t('proxies.reachable')}` : p.last_test_ok === false ? `●${t('proxies.unreachable')}` : `○${t('proxies.untested')}` }}
                </span>
                <span v-if="p.last_tested_at" class="text-[var(--sp-text-muted)] truncate">
                  {{ t('proxies.lastTestedAt', { time: formatTime(p.last_tested_at) }) }}
                </span>
              </div>

              <div v-if="p.reg_country && (!p.country_code || p.reg_country.toUpperCase() !== p.country_code.toUpperCase())" class="text-xs text-amber-600 dark:text-amber-400">
                {{ t('proxies.registeredIn', { name: countryName(p.reg_country) }) }}
              </div>

              <div v-if="p.announced_asn" class="text-xs text-[var(--sp-text-muted)] font-mono truncate">
                {{ p.announced_asn }}<template v-if="p.announced_prefix"> · {{ p.announced_prefix }}</template>
              </div>

              <div class="flex items-center justify-between gap-2 pt-1 border-t border-[var(--sp-border)]">
                <span class="text-xs text-[var(--sp-text-muted)]">
                  {{ t('proxies.accountsBound', { count: p.accounts_count }) }}
                  <template v-if="p.has_password">· {{ t('proxies.hasPassword') }}</template>
                  <template v-if="p.has_secret">· {{ t('proxies.hasSecret') }}</template>
                </span>
                <span class="flex items-center gap-1">
                  <button
                    type="button"
                    class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs cursor-pointer"
                    :disabled="copyingLinkId === p.id"
                    @click="handleCopyProxyLink(p)"
                  >
                    {{ t('proxies.copyLink') }}
                  </button>
                  <button
                    type="button"
                    class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs cursor-pointer"
                    :disabled="testingId === p.id"
                    @click="handleTestProxy(p)"
                  >
                    {{ t('proxies.test') }}
                  </button>
                  <button
                    type="button"
                    class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs cursor-pointer"
                    @click="openRename(p)"
                  >
                    {{ t('proxies.rename') }}
                  </button>
                  <button
                    type="button"
                    class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs !text-rose-600 dark:!text-rose-400 cursor-pointer"
                    @click="handleDeleteProxy(p)"
                  >
                    {{ t('proxies.delete') }}
                  </button>
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- 添加代理弹窗 -->
    <Modal :isOpen="showAddModal" :title="t('proxies.addProxyTitle')" maxWidthClass="max-w-lg" @close="showAddModal = false">
      <div class="space-y-4">
        <div class="flex gap-1 p-1 rounded-[var(--sp-radius)] bg-black/[0.04] dark:bg-white/[0.05]">
          <button
            type="button"
            class="flex-1 px-3 py-1.5 rounded-[var(--sp-radius-sm)] text-xs cursor-pointer"
            :class="addTab === 'paste' ? 'bg-[var(--sp-surface)] shadow-sm font-medium text-[var(--sp-text)]' : 'text-[var(--sp-text-muted)]'"
            @click="addTab = 'paste'"
          >
            {{ t('proxies.pasteTab') }}
          </button>
          <button
            type="button"
            class="flex-1 px-3 py-1.5 rounded-[var(--sp-radius-sm)] text-xs cursor-pointer"
            :class="addTab === 'manual' ? 'bg-[var(--sp-surface)] shadow-sm font-medium text-[var(--sp-text)]' : 'text-[var(--sp-text-muted)]'"
            @click="addTab = 'manual'"
          >
            {{ t('proxies.manualTab') }}
          </button>
        </div>

        <div v-if="addTab === 'paste'" class="space-y-3">
          <textarea
            v-model="importText"
            rows="8"
            class="ui-input font-mono !text-xs resize-y"
            :placeholder="t('proxies.pastePlaceholder')"
          />
          <div class="flex items-center justify-between">
            <span class="text-xs text-[var(--sp-text-muted)]">{{ t('proxies.recognizedCount', { count: recognizedCount }) }}</span>
            <button
              type="button"
              class="ui-btn-primary !px-4 !py-2 !text-xs cursor-pointer"
              :disabled="importing || recognizedCount === 0"
              @click="handleImport"
            >
              {{ importing ? t('common.processing') : t('proxies.import') }}
            </button>
          </div>
        </div>

        <div v-else class="space-y-3">
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-type">{{ t('proxies.typeLabel') }}</label>
              <select id="proxy-add-type" v-model="manualForm.ptype" class="ui-input">
                <option value="socks5">SOCKS5</option>
                <option value="socks4">SOCKS4</option>
                <option value="http">HTTP</option>
                <option value="mtproto">MTProto</option>
              </select>
            </div>
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-port">{{ t('proxies.portLabel') }}</label>
              <input id="proxy-add-port" v-model="manualForm.port" type="number" min="1" max="65535" class="ui-input font-mono" placeholder="1080">
            </div>
          </div>
          <div class="space-y-1.5">
            <label class="ui-label" for="proxy-add-server">{{ t('proxies.serverLabel') }}</label>
            <input id="proxy-add-server" v-model="manualForm.host" type="text" class="ui-input font-mono" placeholder="1.2.3.4">
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-user">{{ t('proxies.usernameLabel') }}（{{ t('common.optional') }}）</label>
              <input id="proxy-add-user" v-model="manualForm.username" type="text" class="ui-input" autocomplete="off">
            </div>
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-pass">{{ t('proxies.passwordLabel') }}（{{ t('common.optional') }}）</label>
              <input id="proxy-add-pass" v-model="manualForm.password" type="password" class="ui-input" autocomplete="new-password">
            </div>
          </div>
          <div v-if="manualForm.ptype === 'mtproto'" class="space-y-1.5">
            <label class="ui-label" for="proxy-add-secret">{{ t('proxies.secretLabel') }}</label>
            <input id="proxy-add-secret" v-model="manualForm.secret" type="password" class="ui-input font-mono" autocomplete="off" placeholder="ee...">
            <p class="text-xs text-[var(--sp-text-muted)]">{{ t('proxies.secretHint') }}</p>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-label">{{ t('proxies.labelLabel') }}（{{ t('common.optional') }}）</label>
              <input id="proxy-add-label" v-model="manualForm.label" type="text" class="ui-input">
            </div>
            <div class="space-y-1.5">
              <label class="ui-label" for="proxy-add-region">{{ t('proxies.regionLabel') }}（{{ t('common.optional') }}）</label>
              <input id="proxy-add-region" v-model="manualForm.region" type="text" class="ui-input font-mono uppercase" :placeholder="t('proxies.regionPlaceholder')">
            </div>
          </div>
          <div class="flex justify-end">
            <button
              type="button"
              class="ui-btn-primary !px-4 !py-2 !text-xs cursor-pointer"
              :disabled="creating"
              @click="handleCreateProxy"
            >
              {{ creating ? t('common.processing') : t('proxies.createProxy') }}
            </button>
          </div>
        </div>
      </div>
    </Modal>

    <!-- 改名弹窗 -->
    <Modal :isOpen="!!renameTarget" :title="t('proxies.renameTitle')" @close="renameTarget = null">
      <div class="space-y-4">
        <div class="space-y-1.5">
          <label class="ui-label" for="proxy-rename-label">{{ t('proxies.renameLabel') }}</label>
          <input id="proxy-rename-label" v-model="renameLabel" type="text" class="ui-input">
          <p class="text-xs text-[var(--sp-text-muted)]">{{ t('proxies.renameEmptyHint') }}</p>
        </div>
      </div>
      <template #footer>
        <button type="button" class="ui-btn-secondary !px-4 !py-2" @click="renameTarget = null">
          {{ t('common.cancel') }}
        </button>
        <button
          type="button"
          class="ui-btn-primary !px-4 !py-2"
          :disabled="renameSaving"
          @click="saveRename"
        >
          {{ t('common.save') }}
        </button>
      </template>
    </Modal>

    <!-- 设置线路弹窗 -->
    <Modal :isOpen="!!routeTarget" :title="routeTarget ? t('proxies.routeModalTitle', { name: routeTarget.account_name }) : ''" @close="routeTarget = null">
      <div class="space-y-4">
        <div v-if="routeError" class="ui-alert-error" role="alert">
          {{ routeError }}
        </div>
        <div class="space-y-2" role="radiogroup" :aria-label="t('proxies.routeModalTitle', { name: routeTarget?.account_name || '' })">
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="routeMode" type="radio" value="inherit" class="!w-auto">
            {{ t('proxies.routeInherit') }}
          </label>
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="routeMode" type="radio" value="direct" class="!w-auto">
            {{ t('proxies.routeDirect') }}
          </label>
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="routeMode" type="radio" value="pool" class="!w-auto">
            {{ t('proxies.routePool') }}
          </label>
        </div>
        <div v-if="routeMode === 'pool'" class="space-y-1.5">
          <label class="ui-label" for="route-proxy-select">{{ t('proxies.routePool') }}</label>
          <select id="route-proxy-select" v-model="routeProxyId" class="ui-input">
            <option value="" disabled>{{ t('proxies.routeSelectPlaceholder') }}</option>
            <option v-for="p in sortedProxies" :key="p.id" :value="p.id">{{ proxyOptionLabel(p) }}</option>
          </select>
        </div>
        <p v-if="routeTarget" class="text-xs text-[var(--sp-text-muted)]">
          {{ t('accounts.idNumber') }}：<span class="font-mono">{{ routeTarget.account_name }}</span>
          · {{ routeTarget.effective_label }}
        </p>
      </div>
      <template #footer>
        <button type="button" class="ui-btn-secondary !px-4 !py-2" @click="routeTarget = null">
          {{ t('common.cancel') }}
        </button>
        <button
          type="button"
          class="ui-btn-primary !px-4 !py-2"
          :disabled="routeSaving || (routeMode === 'pool' && !routeProxyId)"
          @click="saveRoute"
        >
          {{ routeSaving ? t('common.saving') : t('common.save') }}
        </button>
      </template>
    </Modal>
  </div>
</template>
