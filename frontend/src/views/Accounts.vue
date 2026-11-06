<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Play, FileText, Edit2, Trash2, Plus, QrCode, Phone, Zap, MonitorSmartphone, MessageCircle, MessagesSquare, MoreHorizontal, CheckCircle2, Search, RefreshCw, XCircle, X, Users, FileDown, KeyRound, LayoutGrid, List, Eye, EyeOff } from 'lucide-vue-next'
import {
  fetchAccountAvatar,
} from '../lib/api'
import { getAuthToken } from '../lib/api/core'
import { useI18n } from '../composables/useI18n'
import { useToast } from '../composables/useToast'
import { useConfirm } from '../composables/useConfirm'
import { useAccountsStore } from '../stores/accounts'
import { useAccountBatchCheck } from '../composables/useAccountBatchCheck'
import type { AccountUiItem } from '../lib/types'
import { notifyApiError } from '../lib/notify'
import AccountImpactModal from '../components/accounts/AccountImpactModal.vue'
import { previewAccountChange, applyAccountChange, refreshAccountProfile, moveAccount, pauseAccount, resumeAccount, type AccountChangePlan } from '../lib/api/accounts'
import AccountTagsModal from '../components/accounts/AccountTagsModal.vue'
import AddAccountModal from '../components/accounts/AddAccountModal.vue'
import EditAccountModal from '../components/accounts/EditAccountModal.vue'
import DeviceManagerModal from '../components/accounts/DeviceManagerModal.vue'
import OfficialMessagesModal from '../components/accounts/OfficialMessagesModal.vue'
import SessionPackageModal from '../components/accounts/SessionPackageModal.vue'
import PageRetry from '../components/PageRetry.vue'
import FilterEmptyState from '../components/FilterEmptyState.vue'
import { devLog } from '../lib/devLog'
import { AVATAR_FETCH_CONCURRENCY, mapPool } from '../lib/async-pool'
import { AvatarUrlCache } from '../lib/avatar-cache'
import {
  canMoveAccount,
  collectAccountDcIds,
  filterAccountsByDc,
  filterAccountsByQuery,
  filterAccountsByStatus,
  mapAccountInfoToUiItem,
  maskPhone,
  maskUserId,
  sortAccounts,
  type AccountSortKey,
  type AccountStatusFilter,
} from '../lib/account-list-map'
import { copyToClipboard } from '../lib/clipboard'
import { registrationDisplay } from '../lib/registration-estimate'
import { storageGet, storageSet } from '../lib/safe-storage'

const formatDateTime = (s: string | null | undefined): string =>
  s ? s.slice(0, 16).replace('T', ' ') : ''

/** 注册日期第一步：卡片注册行文案与悬停说明（见 src/lib/registration-estimate.ts）。 */
const registrationText = (acc: AccountUiItem): string =>
  registrationDisplay(acc.raw, t, locale.value === 'en').text
const registrationTitle = (acc: AccountUiItem): string =>
  registrationDisplay(acc.raw, t, locale.value === 'en').title

/** 账号卡代理行文案：指定代理名 / 直连 / 自定义线路 / 继承全局代理 */
const proxyLineLabel = (acc: AccountUiItem): string => {
  const raw = acc.raw
  if (raw.proxy_label) return raw.proxy_label
  if (raw.proxy_mode === 'direct') return t('accounts.direct')
  if (raw.proxy_mode === 'custom' && raw.proxy) return raw.proxy
  return t('accounts.inheritGlobal')
}

const router = useRouter()
const { t, locale } = useI18n()
const toast = useToast()
const { confirm } = useConfirm()
const accountsStore = useAccountsStore()
const accounts = ref<AccountUiItem[]>([])
const pageLoading = ref(true)
// 会话内头像 URL 缓存：避免每次刷新重复请求与重复创建 ObjectURL
const avatarCache = new AvatarUrlCache()
// 卸载标记：在途头像请求完成后不再创建 ObjectURL，避免 blob 泄漏
let disposed = false
/** 待重登账号：编辑弹窗离场动画结束后据此打开添加弹窗 */
const pendingReloginName = ref('')
const showAddModal = ref(false)
const showEditModal = ref(false)
const showAddMenu = ref(false)
const initialMethod = ref<'code' | 'qr' | 'import' | 'passkey'>('code')
const initialAccountName = ref('')
const editingAccount = ref<AccountUiItem | null>(null)
const showDeviceModal = ref(false)
const deviceAccountName = ref('')
const showOfficialMessagesModal = ref(false)
const officialMessagesAccountName = ref('')
const searchQuery = ref('')
const loadError = ref(false)

/** 排序与显示方式存 localStorage；状态/机房筛选只影响本次会话 */
const SORT_STORAGE_KEY = 'tg-signpulse-accounts-sort'
const VIEW_STORAGE_KEY = 'tg-signpulse-accounts-view'
const SORT_KEYS: AccountSortKey[] = ['custom', 'abnormal', 'oldestChecked', 'name']
const readSortKey = (): AccountSortKey => {
  const raw = storageGet(SORT_STORAGE_KEY)
  return (SORT_KEYS as string[]).includes(raw ?? '') ? (raw as AccountSortKey) : 'custom'
}
const sortKey = ref<AccountSortKey>(readSortKey())
const viewMode = ref<'card' | 'list'>(storageGet(VIEW_STORAGE_KEY) === 'list' ? 'list' : 'card')
watch(sortKey, (v) => storageSet(SORT_STORAGE_KEY, v))
watch(viewMode, (v) => storageSet(VIEW_STORAGE_KEY, v))
const statusFilter = ref<AccountStatusFilter>('all')
const dcFilter = ref<number | null>(null)
/** 全局明文开关（仅本次会话，刷新后恢复掩码）与单卡点按显示 */
const showAllUserIds = ref(false)
const showAllPhones = ref(false)
const revealedUserIds = ref<ReadonlySet<string>>(new Set())
const revealedPhones = ref<ReadonlySet<string>>(new Set())

const onSortChange = (e: Event) => {
  const v = (e.target as HTMLSelectElement).value
  sortKey.value = (SORT_KEYS as string[]).includes(v) ? (v as AccountSortKey) : 'custom'
}
const onDcFilterChange = (e: Event) => {
  const v = (e.target as HTMLSelectElement).value
  dcFilter.value = v === '' ? null : Number(v)
}

const dcOptions = computed(() => collectAccountDcIds(accounts.value))

const filteredAccounts = computed(() => {
  const byQuery = filterAccountsByQuery(accounts.value, searchQuery.value)
  const byStatus = filterAccountsByStatus(byQuery, statusFilter.value)
  const byDc = filterAccountsByDc(byStatus, dcFilter.value)
  return sortAccounts(byDc, sortKey.value)
})

const hasListFilters = computed(
  () => searchQuery.value.trim().length > 0 || statusFilter.value !== 'all' || dcFilter.value !== null,
)

const clearListFilters = () => {
  searchQuery.value = ''
  statusFilter.value = 'all'
  dcFilter.value = null
}

const phoneText = (acc: AccountUiItem): string => {
  if (!acc.raw.phone) return ''
  return showAllPhones.value || revealedPhones.value.has(acc.id)
    ? acc.raw.phone
    : maskPhone(acc.raw.phone)
}
const userIdText = (acc: AccountUiItem): string => {
  if (acc.raw.user_id === null || acc.raw.user_id === undefined) return ''
  return showAllUserIds.value || revealedUserIds.value.has(acc.id)
    ? String(acc.raw.user_id)
    : maskUserId(acc.raw.user_id)
}
const copyPlain = async (text: string) => {
  if (await copyToClipboard(text)) toast.success(t('common.copied'))
}
/** 卡片名称：Telegram 账户名优先（first_name → username），缺失才用面板名；
 * 只改显示口径，acc.name 仍是账号的唯一键 */
const displayName = (acc: AccountUiItem): string =>
  acc.raw.first_name?.trim() || acc.raw.username?.trim() || acc.name
/** 点一次显示明文并复制，再点隐藏；全局明文开启时只复制 */
const toggleRevealPhone = async (acc: AccountUiItem) => {
  if (!acc.raw.phone) return
  if (showAllPhones.value) {
    await copyPlain(acc.raw.phone)
    return
  }
  const next = new Set(revealedPhones.value)
  if (next.has(acc.id)) {
    next.delete(acc.id)
  } else {
    next.add(acc.id)
    await copyPlain(acc.raw.phone)
  }
  revealedPhones.value = next
}
const toggleRevealUserId = async (acc: AccountUiItem) => {
  if (acc.raw.user_id === null || acc.raw.user_id === undefined) return
  const plain = String(acc.raw.user_id)
  if (showAllUserIds.value) {
    await copyPlain(plain)
    return
  }
  const next = new Set(revealedUserIds.value)
  if (next.has(acc.id)) {
    next.delete(acc.id)
  } else {
    next.add(acc.id)
    await copyPlain(plain)
  }
  revealedUserIds.value = next
}

/** 账号管理页为单一事实来源：每次调用都强制刷新（增删改/检测后保持一致） */
const loadAccounts = async () => {
  const token = getAuthToken()
  if (!token) return

  try {
    loadError.value = false
    const list = await accountsStore.refreshAccounts()
    const labels = {
      loginExpired: t('accounts.loginExpired'),
      checking: t('accounts.checking'),
    }
    // 保持服务端返回的原始顺序（即排序下拉里的“自定义顺序”）；名称排序走 computed
    accounts.value = list.map((acc) => {
      const ui = mapAccountInfoToUiItem(acc, labels)
      // 复用已加载的头像 URL，未缓存项交由 loadAvatars 补充
      const cached = avatarCache.get(acc.name)
      if (cached) ui.avatarUrl = cached
      return ui
    })
    // 限流加载头像，避免账号多时并发打满连接
    void loadAvatars(accounts.value)
    // 身份回填（见 backfillMissingIdentity）：首屏渲染不等待
    void backfillMissingIdentity()
  } catch (e: unknown) {
    devLog.error('Failed to fetch accounts', e)
    loadError.value = true
    notifyApiError(e, 'accounts.loadFailed')
  } finally {
    pageLoading.value = false
  }
}

/** 身份回填：phone / first_name / username 只在 get_me 成功后才缓存进 DB。
 * 从未跑过状态检测/刷新资料的账号根本没有手机号可显示——卡片上没有手机号行，
 * “显示全部手机号”开关点了自然没效果（开关逻辑本身没坏）。
 * 首次加载后对缺失身份的账号逐个调一次 refresh-profile（已有接口，顺序、不并发），
 * 有账号补上身份后重载一次列表；单个失败静默跳过，不弹错、不阻塞首屏。
 * 每挂载一次最多跑一轮；开关本身只做本次会话显示，不改存储。 */
const identityBackfilled = ref(false)
const backfillMissingIdentity = async () => {
  if (identityBackfilled.value) return
  identityBackfilled.value = true
  const token = getAuthToken()
  if (!token) return
  const missing = accounts.value.filter(
    (a) => !a.raw.phone || (!a.raw.first_name && !a.raw.username),
  )
  if (missing.length === 0) return
  let changed = false
  for (const acc of missing) {
    try {
      await refreshAccountProfile(token, acc.name)
      changed = true
    } catch {
      // 会话失效等原因拿不到身份：保持未知，不阻塞其他账号
    }
  }
  if (changed) await loadAccounts()
}

const loadAvatar = async (acc: AccountUiItem) => {
  const token = getAuthToken()
  try {
    let url = avatarCache.get(acc.name)
    if (!url) {
      const blob = await fetchAccountAvatar(token, acc.name)
      if (disposed) return // 组件已卸载：不再创建 ObjectURL，避免 blob 泄漏
      url = URL.createObjectURL(blob)
      avatarCache.set(acc.name, url)
    }
    acc.avatarUrl = url
  } catch {
    // 头像下载失败/无头像：保留首字母占位，不影响列表
    devLog.info('头像加载失败，保留占位:', acc.name)
  }
}

const loadAvatars = async (list: AccountUiItem[]) => {
  await mapPool(list, AVATAR_FETCH_CONCURRENCY, async (acc) => {
    await loadAvatar(acc)
  })
}

onMounted(async () => {
  await loadAccounts()
  // 刷新页面后恢复未完成的批量检测
  void resumeActiveBatchJob()
})

onUnmounted(() => {
  disposed = true
  // 离开页面时统一回收会话内头像 ObjectURL，避免 blob 泄漏
  avatarCache.release()
})

const changePlan = ref<AccountChangePlan | null>(null)
const changeLoading = ref(false)
const changeError = ref('')
const handleDelete = async (name: string) => {
  try {
    changeError.value = ''
    changePlan.value = await previewAccountChange(getAuthToken(), name, 'delete')
  } catch (e: unknown) {
    notifyApiError(e, 'accounts.deleteFailed')
  }
}
const applyDelete = async () => {
  if (!changePlan.value) return
  changeLoading.value = true
  try {
    await applyAccountChange(getAuthToken(), changePlan.value)
    changePlan.value = null
    toast.success(t('accounts.deleteSuccess'))
    await loadAccounts()
  } catch (e: unknown) {
    changeError.value = e instanceof Error ? e.message : String(e)
  } finally {
    changeLoading.value = false
  }
}

const {
  checkingAccount,
  batchChecking,
  batchJob,
  batchProgressPct,
  lastFailedAccountNames,
  handleCheck,
  handleBatchCheck,
  handleCancelBatchCheck,
  handleRecheckFailed,
  resumeActiveBatchJob,
} = useAccountBatchCheck({
  accounts,
  filteredAccounts,
  searchQuery,
  loadAccounts,
  isFiltered: hasListFilters,
})

const openEdit = (acc: AccountUiItem) => {
  editingAccount.value = acc
  showEditModal.value = true
}

const openDevices = (name: string) => {
  deviceAccountName.value = name
  showDeviceModal.value = true
}

const openOfficialMessages = (name: string) => {
  officialMessagesAccountName.value = name
  showOfficialMessagesModal.value = true
}

const handleRelogin = (name: string) => {
  showEditModal.value = false
  // 不立刻打开添加弹窗：等编辑弹窗离场动画结束再由 onEditModalClosed 接续，
  // 避免两层弹窗短暂重叠、焦点恢复与自动聚焦互相抢占
  pendingReloginName.value = name
}

const onEditModalClosed = () => {
  if (!pendingReloginName.value) return
  initialAccountName.value = pendingReloginName.value
  initialMethod.value = 'code'
  pendingReloginName.value = ''
  showAddModal.value = true
}

const openAddModal = (method: 'code' | 'qr' | 'import' | 'passkey') => {
  initialAccountName.value = ''
  initialMethod.value = method
  showAddMenu.value = false
  showAddModal.value = true
}

const goLogs = (name: string) => {
  router.push({ name: 'logs', query: { account: name } })
}

const goTasks = (name: string) => {
  router.push({ name: 'tasks', query: { account: name } })
}

const goChats = (name: string) => {
  router.push({ name: 'chats', query: { account: name } })
}

/** 卡片“···”菜单：同一时刻只开一个；点菜单外任意处或按 Esc 关闭 */
const openMenuName = ref<string | null>(null)
const toggleMenu = (name: string) => {
  openMenuName.value = openMenuName.value === name ? null : name
}
const closeMenu = () => {
  openMenuName.value = null
}

/** 上移/下移按完整自定义顺序（服务端返回顺序）判定，与筛选/排序下拉无关 */
const accountOrderIndex = (acc: AccountUiItem): number =>
  accounts.value.findIndex((a) => a.name === acc.name)
const moveState = (acc: AccountUiItem) =>
  canMoveAccount(accountOrderIndex(acc), accounts.value.length)

const handleRefreshProfile = async (acc: AccountUiItem) => {
  closeMenu()
  try {
    await refreshAccountProfile(getAuthToken(), acc.name)
    toast.success(t('accounts.refreshProfileSuccess'))
    await loadAccounts()
  } catch (e: unknown) {
    notifyApiError(e, 'accounts.refreshProfileFailed')
  }
}

const handleMove = async (acc: AccountUiItem, direction: 'up' | 'down') => {
  closeMenu()
  try {
    const res = await moveAccount(getAuthToken(), acc.name, direction)
    if (res.moved) {
      await loadAccounts()
    } else {
      toast.info(direction === 'up' ? t('accounts.moveTopReached') : t('accounts.moveBottomReached'))
    }
  } catch (e: unknown) {
    notifyApiError(e, 'accounts.moveFailed')
  }
}

const handlePauseToggle = async (acc: AccountUiItem) => {
  closeMenu()
  try {
    if (acc.raw.paused) {
      await resumeAccount(getAuthToken(), acc.name)
      toast.success(t('accounts.resumeAccountSuccess'))
    } else {
      const ok = await confirm({
        title: t('accounts.pauseAccountConfirmTitle'),
        message: t('accounts.pauseAccountConfirmMessage'),
      })
      if (!ok) return
      await pauseAccount(getAuthToken(), acc.name)
      toast.success(t('accounts.pauseAccountSuccess'))
    }
    await loadAccounts()
  } catch (e: unknown) {
    notifyApiError(e, acc.raw.paused ? 'accounts.resumeAccountFailed' : 'accounts.pauseAccountFailed')
  }
}

/** 标签弹窗：append 卡片“+ 添加标签”（只追加），edit 菜单“编辑标签”（全量） */
const showTagsModal = ref(false)
const tagsAccount = ref<AccountUiItem | null>(null)
const tagsMode = ref<'append' | 'edit'>('append')
const openTagsModal = (acc: AccountUiItem, mode: 'append' | 'edit') => {
  closeMenu()
  tagsAccount.value = acc
  tagsMode.value = mode
  showTagsModal.value = true
}

/** 导出会话包（直接复制当前 auth_key 的 ZIP）：密码确认弹窗直达下载 */
const showPackageModal = ref(false)
const packageAccountName = ref('')
const handleExportSession = (acc: AccountUiItem) => {
  closeMenu()
  packageAccountName.value = acc.name
  showPackageModal.value = true
}
</script>

<template>
  <div class="relative min-h-[80vh]">
    <!-- Page Loading skeleton -->
    <div v-if="pageLoading" class="space-y-4" aria-busy="true">
      <div class="ui-card p-3 flex justify-between">
        <div class="ui-skeleton h-4 w-24" />
        <div class="ui-skeleton h-8 w-28" />
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
        <div v-for="i in 4" :key="i" class="ui-card p-5 space-y-4">
          <div class="flex items-center gap-3">
            <div class="ui-skeleton w-10 h-10 shrink-0 rounded-[var(--sp-radius)]" />
            <div class="flex-1 space-y-2">
              <div class="ui-skeleton h-3.5 w-24" />
              <div class="ui-skeleton h-3 w-16" />
            </div>
          </div>
          <div class="ui-skeleton h-10 w-full" />
        </div>
      </div>
    </div>

    <!-- 加载失败（空列表时也要能重试，不能误显示 empty） -->
    <div v-else-if="loadError" class="space-y-4">
      <PageRetry
        :message="t('accounts.loadFailed')"
        :loading="pageLoading"
        @retry="pageLoading = true; loadAccounts()"
      />
    </div>

    <!-- Empty State -->
    <div v-else-if="accounts.length === 0" class="ui-empty">
      <div class="ui-empty-icon">
        <Users class="w-8 h-8" />
      </div>
      <p class="ui-empty-title">{{ t('accounts.empty') }}</p>
      <p class="ui-empty-desc mb-4">{{ t('accounts.emptyHint') }}</p>
      <div class="flex flex-wrap items-center justify-center gap-2">
        <button type="button" class="ui-btn-primary !text-xs !px-3 !py-2 cursor-pointer" @click="openAddModal('code')">
          <Phone class="w-3.5 h-3.5" /> {{ t('accounts.codeLogin') }}
        </button>
        <button type="button" class="ui-btn-secondary !text-xs !px-3 !py-2 cursor-pointer" @click="openAddModal('qr')">
          <QrCode class="w-3.5 h-3.5" /> {{ t('accounts.qrLogin') }}
        </button>
      </div>
    </div>

    <div v-else class="space-y-4 pb-20">
      <div class="ui-card flex flex-col sm:flex-row sm:items-center gap-3 p-3">
        <div class="text-xs text-[var(--sp-text-muted)] shrink-0">
          {{ t('accounts.total') }}：<span class="font-mono text-[var(--sp-text)] font-semibold">{{ filteredAccounts.length }}</span>
          <span v-if="hasListFilters" class="text-[var(--sp-text-muted)]"> / {{ accounts.length }}</span>
        </div>
        <div class="relative flex-1 min-w-0 max-w-md">
          <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--sp-text-muted)] pointer-events-none" />
          <input
            v-model="searchQuery"
            type="search"
            class="ui-input !pl-8 !h-9 !text-xs"
            :class="searchQuery.trim() ? '!pr-8' : ''"
            :placeholder="t('common.searchPlaceholder')"
            :aria-label="t('common.search')"
          >
          <button
            v-if="searchQuery.trim()"
            type="button"
            class="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 text-[var(--sp-text-muted)] hover:text-[var(--sp-text-secondary)] dark:hover:text-[var(--sp-text)] rounded-[var(--sp-radius-sm)] cursor-pointer"
            :title="t('common.clearFilters')"
            :aria-label="t('common.clearFilters')"
            @click="clearListFilters"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <div
            v-if="batchChecking && batchJob"
            class="flex flex-col items-end gap-0.5 min-w-0 sm:min-w-[7rem]"
          >
            <span class="text-[11px] font-mono text-sky-700 dark:text-sky-300 whitespace-nowrap">
              {{ t('accounts.batchCheckProgress', {
                done: batchJob.progress?.done ?? 0,
                total: batchJob.progress?.total ?? accounts.length,
              }) }}
              <template v-if="(batchJob.progress?.ok ?? 0) + (batchJob.progress?.fail ?? 0) > 0">
                · {{ t('accounts.batchCheckOkFail', {
                  ok: batchJob.progress?.ok ?? 0,
                  fail: batchJob.progress?.fail ?? 0,
                }) }}
              </template>
            </span>
            <div
              class="hidden sm:block w-full h-1 rounded-full bg-sky-100 dark:bg-sky-950/50 overflow-hidden"
              role="progressbar"
              :aria-valuenow="batchProgressPct"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-label="t('accounts.batchCheckProgress', {
                done: batchJob.progress?.done ?? 0,
                total: batchJob.progress?.total ?? accounts.length,
              })"
            >
              <div
                class="h-full bg-sky-500 transition-all duration-300"
                :style="{ width: `${batchProgressPct}%` }"
                aria-hidden="true"
              />
            </div>
          </div>
          <button
            v-if="batchChecking && batchJob?.job_id"
            type="button"
            class="ui-btn-secondary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
            @click="handleCancelBatchCheck"
          >
            <XCircle class="w-3.5 h-3.5" />
            {{ t('accounts.batchCheckCancel') }}
          </button>
          <button
            v-if="!batchChecking && lastFailedAccountNames.length > 0"
            type="button"
            class="ui-btn-secondary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
            :title="t('accounts.batchRecheckFailedHint')"
            @click="handleRecheckFailed"
          >
            <RefreshCw class="w-3.5 h-3.5" />
            {{ t('accounts.batchRecheckFailed') }}
            <span class="font-mono opacity-80">({{ lastFailedAccountNames.length }})</span>
          </button>
          <button
            type="button"
            class="ui-btn-primary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
            :disabled="batchChecking"
            :title="batchChecking ? t('accounts.batchChecking') : undefined"
            @click="handleBatchCheck"
          >
            <span v-if="batchChecking" class="ui-spinner !w-3.5 !h-3.5 !border-2" />
            <CheckCircle2 v-else class="w-3.5 h-3.5" />
            {{ batchChecking ? t('accounts.batchChecking') : t('accounts.batchCheck') }}
          </button>
        </div>
      </div>
      <!-- 浏览与整理：排序 / 状态筛选 / 机房筛选 / 显示方式 / 明文开关 -->
      <div class="ui-card flex flex-wrap items-center gap-2 sm:gap-3 p-3">
        <label class="flex items-center gap-1.5 text-xs text-[var(--sp-text-muted)]">
          {{ t('accounts.sortLabel') }}
          <select :value="sortKey" class="ui-input !h-8 !w-auto !text-xs" :aria-label="t('accounts.sortLabel')" @change="onSortChange">
            <option value="custom">{{ t('accounts.sortCustom') }}</option>
            <option value="abnormal">{{ t('accounts.sortAbnormal') }}</option>
            <option value="oldestChecked">{{ t('accounts.sortOldestChecked') }}</option>
            <option value="name">{{ t('accounts.sortName') }}</option>
          </select>
        </label>
        <div class="flex items-center rounded-[var(--sp-radius)] border border-[var(--sp-border)] overflow-hidden" role="group" :aria-label="t('accounts.statusFilterLabel')">
          <button
            type="button"
            class="px-2.5 py-1.5 text-xs cursor-pointer"
            :class="statusFilter === 'all' ? 'bg-sky-500 text-white' : 'text-[var(--sp-text-secondary)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'"
            :aria-pressed="statusFilter === 'all'"
            @click="statusFilter = 'all'"
          >{{ t('accounts.filterAll') }}</button>
          <button
            type="button"
            class="px-2.5 py-1.5 text-xs cursor-pointer border-l border-[var(--sp-border)]"
            :class="statusFilter === 'available' ? 'bg-sky-500 text-white' : 'text-[var(--sp-text-secondary)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'"
            :aria-pressed="statusFilter === 'available'"
            @click="statusFilter = 'available'"
          >{{ t('accounts.filterAvailable') }}</button>
          <button
            type="button"
            class="px-2.5 py-1.5 text-xs cursor-pointer border-l border-[var(--sp-border)]"
            :class="statusFilter === 'offline' ? 'bg-sky-500 text-white' : 'text-[var(--sp-text-secondary)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'"
            :aria-pressed="statusFilter === 'offline'"
            @click="statusFilter = 'offline'"
          >{{ t('accounts.filterOffline') }}</button>
        </div>
        <label class="flex items-center gap-1.5 text-xs text-[var(--sp-text-muted)]">
          {{ t('accounts.dcFilterLabel') }}
          <select :value="dcFilter === null ? '' : String(dcFilter)" class="ui-input !h-8 !w-auto !text-xs" :aria-label="t('accounts.dcFilterLabel')" @change="onDcFilterChange">
            <option value="">{{ t('accounts.dcFilterAll') }}</option>
            <option v-for="dc in dcOptions" :key="dc" :value="String(dc)">DC {{ dc }}</option>
          </select>
        </label>
        <div class="flex items-center rounded-[var(--sp-radius)] border border-[var(--sp-border)] overflow-hidden" role="group" :aria-label="t('accounts.viewModeLabel')">
          <button
            type="button"
            class="p-1.5 cursor-pointer"
            :class="viewMode === 'card' ? 'bg-sky-500 text-white' : 'text-[var(--sp-text-secondary)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'"
            :aria-pressed="viewMode === 'card'"
            :title="t('accounts.viewCard')"
            @click="viewMode = 'card'"
          ><LayoutGrid class="w-4 h-4" /></button>
          <button
            type="button"
            class="p-1.5 cursor-pointer border-l border-[var(--sp-border)]"
            :class="viewMode === 'list' ? 'bg-sky-500 text-white' : 'text-[var(--sp-text-secondary)] hover:bg-black/[0.04] dark:hover:bg-white/[0.06]'"
            :aria-pressed="viewMode === 'list'"
            :title="t('accounts.viewList')"
            @click="viewMode = 'list'"
          ><List class="w-4 h-4" /></button>
        </div>
        <button
          type="button"
          class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer"
          :aria-pressed="showAllUserIds"
          @click="showAllUserIds = !showAllUserIds"
        >
          <Eye v-if="!showAllUserIds" class="w-3.5 h-3.5" />
          <EyeOff v-else class="w-3.5 h-3.5" />
          {{ showAllUserIds ? t('accounts.hideAllUserIds') : t('accounts.showAllUserIds') }}
        </button>
        <button
          type="button"
          class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer"
          :aria-pressed="showAllPhones"
          @click="showAllPhones = !showAllPhones"
        >
          <Eye v-if="!showAllPhones" class="w-3.5 h-3.5" />
          <EyeOff v-else class="w-3.5 h-3.5" />
          {{ showAllPhones ? t('accounts.hideAllPhones') : t('accounts.showAllPhones') }}
        </button>
      </div>
      <div v-if="filteredAccounts.length === 0" class="ui-empty !py-12">
        <FilterEmptyState
          v-if="accounts.length > 0 && hasListFilters"
          :title="t('common.filterNoResults')"
          :hint="t('common.filterNoResultsHint')"
          :action-text="t('common.clearFilters')"
          @action="clearListFilters"
        />
        <p v-else class="ui-empty-desc">{{ t('common.noData') }}</p>
      </div>
      <div v-else-if="viewMode === 'list'" class="ui-card divide-y divide-[var(--sp-border)]">
        <div
          v-for="acc in filteredAccounts" :key="acc.id"
          class="flex flex-wrap items-center gap-3 px-4 py-3"
        >
          <div class="w-9 h-9 shrink-0 bg-[var(--sp-surface-muted)] rounded-[var(--sp-radius)] flex items-center justify-center text-xs text-[var(--sp-text-muted)] font-mono border border-[var(--sp-border)] overflow-hidden">
            <img
              v-if="acc.avatarUrl"
              :src="acc.avatarUrl"
              :alt="displayName(acc)"
              class="w-full h-full object-cover"
              loading="lazy"
              decoding="async"
            >
            <span v-else>{{ displayName(acc).substring(0, 2) }}</span>
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-sm font-medium text-[var(--sp-text)] truncate" :title="acc.name">{{ displayName(acc) }}</span>
              <span
                class="ui-badge shrink-0"
                :class="{
                  'ui-badge-success': acc.status === 'active',
                  'ui-badge-warn': acc.status === 'empty',
                  'ui-badge-error': acc.status === 'error',
                }"
                :title="acc.message || (acc.status === 'active' ? t('accounts.statusOk') : '')"
              >
                <span class="ui-badge-dot" />
                <span class="truncate">
                  {{ acc.status === 'active' ? t('accounts.statusOk') : (acc.message || t('accounts.statusUnknown')) }}
                </span>
              </span>
              <span v-if="acc.raw.paused" class="ui-badge ui-badge-warn shrink-0">
                <span class="ui-badge-dot" />
                <span class="truncate">{{ t('accounts.pausedBadge') }}</span>
              </span>
            </div>
            <div class="text-xs text-[var(--sp-text-muted)] truncate mt-0.5">
              <span v-if="acc.raw.phone" class="font-mono cursor-pointer" :title="t('accounts.clickToReveal')" @click="toggleRevealPhone(acc)">{{ phoneText(acc) }}</span>
              <span v-if="acc.raw.user_id !== null && acc.raw.user_id !== undefined" class="font-mono cursor-pointer" :title="t('accounts.clickToReveal')" @click="toggleRevealUserId(acc)"> · {{ userIdText(acc) }}</span>
              <span v-if="acc.raw.dc_id !== null && acc.raw.dc_id !== undefined"> · DC {{ acc.raw.dc_id }}<template v-if="acc.raw.dc_location"> · {{ acc.raw.dc_location }}</template></span>
              <span> · {{ proxyLineLabel(acc) }}</span>
              <span> · {{ acc.raw.status_checked_at ? formatDateTime(acc.raw.status_checked_at) : t('accounts.neverChecked') }}</span>
            </div>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :disabled="checkingAccount === acc.name" :title="t('accounts.checkStatus')" @click="handleCheck(acc.name)">
              <span v-if="checkingAccount === acc.name" class="ui-spinner !w-3.5 !h-3.5 !border-2" />
              <Play v-else class="w-3.5 h-3.5" />
              <span>{{ t('accounts.check') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.chat')" @click="goChats(acc.name)">
              <MessagesSquare class="w-3.5 h-3.5" />
              <span>{{ t('accounts.chat') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.viewTasks')" @click="goTasks(acc.name)">
              <Zap class="w-3.5 h-3.5" />
              <span>{{ t('accounts.tasks') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.viewLogs')" @click="goLogs(acc.name)">
              <FileText class="w-3.5 h-3.5" />
              <span>{{ t('accounts.logs') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.devices')" @click="openDevices(acc.name)">
              <MonitorSmartphone class="w-3.5 h-3.5" />
              <span>{{ t('accounts.devicesShort') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.officialMessages')" @click="openOfficialMessages(acc.name)">
              <MessageCircle class="w-3.5 h-3.5" />
              <span>{{ t('accounts.officialMessagesShort') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :title="t('accounts.edit')" @click="openEdit(acc)">
              <Edit2 class="w-3.5 h-3.5" />
              <span>{{ t('accounts.editBtn') }}</span>
            </button>
            <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer !text-rose-600 dark:!text-rose-400" :title="t('accounts.deleteBtn')" @click="handleDelete(acc.name)">
              <Trash2 class="w-3.5 h-3.5" />
              <span>{{ t('accounts.deleteBtn') }}</span>
            </button>
            <div class="relative">
              <button type="button" class="ui-btn-secondary !px-2 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer" :aria-expanded="openMenuName === acc.name" :aria-label="t('accounts.moreActions')" :title="t('accounts.moreActions')" @click="toggleMenu(acc.name)">
                <MoreHorizontal class="w-3.5 h-3.5" />
              </button>
              <div v-if="openMenuName === acc.name" class="fixed inset-0 z-40" aria-hidden="true" @click="closeMenu" />
              <div v-if="openMenuName === acc.name" class="absolute right-0 top-full mt-1 z-50 min-w-44 max-h-[70vh] overflow-y-auto ui-card p-1 shadow-[var(--sp-shadow-md)]" role="menu">
                <button type="button" role="menuitem" class="ui-menu-item" @click="handleRelogin(acc.name)">{{ t('accounts.relogin') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="handleRefreshProfile(acc)">{{ t('accounts.refreshProfile') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="openTagsModal(acc, 'edit')">{{ t('accounts.editTags') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" :disabled="!moveState(acc).canMoveUp" @click="handleMove(acc, 'up')">{{ t('accounts.moveUp') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" :disabled="!moveState(acc).canMoveDown" @click="handleMove(acc, 'down')">{{ t('accounts.moveDown') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="handlePauseToggle(acc)">{{ acc.raw.paused ? t('accounts.resumeAccount') : t('accounts.pauseAccount') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="openEdit(acc)">{{ t('accounts.nameRemarkProxy') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="handleExportSession(acc)">{{ t('accounts.exportSessionPackage') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="goLogs(acc.name)">{{ t('accounts.runLogs') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="openDevices(acc.name)">{{ t('accounts.devicesAndSessions') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item" @click="openOfficialMessages(acc.name)">{{ t('accounts.officialMessages') }}</button>
                <button type="button" role="menuitem" class="ui-menu-item !text-rose-600 dark:!text-rose-400" @click="handleDelete(acc.name)">{{ t('accounts.deleteBtn') }}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
    <div
      v-for="acc in filteredAccounts" :key="acc.id"
      class="ui-card ui-card-hover ui-contain-account group relative flex flex-col p-5"
    >
      <div class="flex justify-between items-start mb-4">
        <div class="flex items-center gap-3 truncate max-w-[70%]">
          <div class="w-10 h-10 shrink-0 bg-[var(--sp-surface-muted)] rounded-[var(--sp-radius)] flex items-center justify-center text-xs text-[var(--sp-text-muted)] font-mono border border-[var(--sp-border)] overflow-hidden">
            <img 
              v-if="acc.avatarUrl" 
              :src="acc.avatarUrl" 
              :alt="displayName(acc)"
              class="w-full h-full object-cover"
              loading="lazy"
              decoding="async"
            />
            <span v-else>{{ displayName(acc).substring(0, 2) }}</span>
          </div>
          <div class="truncate">
            <div class="text-sm font-medium text-[var(--sp-text)] truncate" :title="acc.name">{{ displayName(acc) }}</div>
            <div class="text-xs text-[var(--sp-text-muted)] mt-0.5 font-mono truncate" :title="acc.remark || t('accounts.noRemark')">{{ acc.remark || t('accounts.noRemark') }}</div>
          </div>
        </div>
        
        <!-- Status Indicator -->
        <div class="flex items-center gap-1.5 shrink-0 max-w-[45%]">
          <span
            class="ui-badge max-w-full"
            :class="{
              'ui-badge-success': acc.status === 'active',
              'ui-badge-warn': acc.status === 'empty',
              'ui-badge-error': acc.status === 'error',
            }"
            :title="acc.message || (acc.status === 'active' ? t('accounts.statusOk') : '')"
          >
            <span class="ui-badge-dot" />
            <span class="truncate">
              {{ acc.status === 'active' ? t('accounts.statusOk') : (acc.message || t('accounts.statusUnknown')) }}
            </span>
          </span>
          <span v-if="acc.raw.paused" class="ui-badge ui-badge-warn shrink-0">
            <span class="ui-badge-dot" />
            <span class="truncate">{{ t('accounts.pausedBadge') }}</span>
          </span>
          <div class="relative shrink-0">
            <button type="button" class="ui-btn-secondary !px-1.5 !py-1.5 !text-xs inline-flex items-center cursor-pointer" :aria-expanded="openMenuName === acc.name" :aria-label="t('accounts.moreActions')" :title="t('accounts.moreActions')" @click="toggleMenu(acc.name)">
              <MoreHorizontal class="w-3.5 h-3.5" />
            </button>
            <div v-if="openMenuName === acc.name" class="fixed inset-0 z-40" aria-hidden="true" @click="closeMenu" />
            <div v-if="openMenuName === acc.name" class="absolute right-0 top-full mt-1 z-50 min-w-44 max-h-[70vh] overflow-y-auto ui-card p-1 shadow-[var(--sp-shadow-md)]" role="menu">
              <button type="button" role="menuitem" class="ui-menu-item" @click="handleRelogin(acc.name)">{{ t('accounts.relogin') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="handleRefreshProfile(acc)">{{ t('accounts.refreshProfile') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="openTagsModal(acc, 'edit')">{{ t('accounts.editTags') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" :disabled="!moveState(acc).canMoveUp" @click="handleMove(acc, 'up')">{{ t('accounts.moveUp') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" :disabled="!moveState(acc).canMoveDown" @click="handleMove(acc, 'down')">{{ t('accounts.moveDown') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="handlePauseToggle(acc)">{{ acc.raw.paused ? t('accounts.resumeAccount') : t('accounts.pauseAccount') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="openEdit(acc)">{{ t('accounts.nameRemarkProxy') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="handleExportSession(acc)">{{ t('accounts.exportSessionPackage') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="goLogs(acc.name)">{{ t('accounts.runLogs') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="openDevices(acc.name)">{{ t('accounts.devicesAndSessions') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item" @click="openOfficialMessages(acc.name)">{{ t('accounts.officialMessages') }}</button>
              <button type="button" role="menuitem" class="ui-menu-item !text-rose-600 dark:!text-rose-400" @click="handleDelete(acc.name)">{{ t('accounts.deleteBtn') }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 账号信息行（raw 透传后端扩展字段；缺失的字段整行省略，注册行除外） -->
      <div class="space-y-1 mb-4 text-xs">
        <div class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.phoneNumber') }}：</span>
          <span v-if="acc.raw.phone" class="text-[var(--sp-text-secondary)] font-mono truncate cursor-pointer" :title="t('accounts.clickToReveal')" @click="toggleRevealPhone(acc)">{{ phoneText(acc) }}</span>
          <span v-else class="text-[var(--sp-text-secondary)] truncate">{{ t('accounts.phoneUnknown') }}</span>
        </div>
        <div v-if="acc.raw.user_id !== null && acc.raw.user_id !== undefined" class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.userId') }}：</span>
          <span class="text-[var(--sp-text-secondary)] font-mono truncate cursor-pointer" :title="t('accounts.clickToReveal')" @click="toggleRevealUserId(acc)">{{ userIdText(acc) }}</span>
        </div>
        <div v-if="acc.raw.dc_id !== null && acc.raw.dc_id !== undefined" class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.dc') }}：</span>
          <span class="text-[var(--sp-text-secondary)] truncate">DC {{ acc.raw.dc_id }}<template v-if="acc.raw.dc_location"> · {{ acc.raw.dc_location }}</template></span>
        </div>
        <div v-if="acc.raw.device_profile" class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.device') }}：</span>
          <span class="text-[var(--sp-text-secondary)] truncate">{{ acc.raw.device_profile.device_model }} · {{ acc.raw.device_profile.system_version }}</span>
        </div>
        <div class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.registeredAt') }}：</span>
          <span class="text-[var(--sp-text-secondary)] truncate" :title="registrationTitle(acc)">{{ registrationText(acc) }}</span>
        </div>
        <div class="flex gap-1.5 items-start">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.tags') }}：</span>
          <span class="flex flex-wrap items-center gap-1 min-w-0">
            <span v-for="tag in acc.raw.tags" :key="tag" class="ui-badge !py-0 !px-1.5">{{ tag }}</span>
            <button type="button" class="text-xs text-[var(--sp-accent)] hover:underline cursor-pointer" @click="openTagsModal(acc, 'append')">+ {{ t('accounts.addTags') }}</button>
          </span>
        </div>
        <div class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.proxy') }}：</span>
          <span class="text-[var(--sp-text-secondary)] truncate" :title="proxyLineLabel(acc)">{{ proxyLineLabel(acc) }}</span>
        </div>
        <div class="flex gap-1.5">
          <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('accounts.checkedAt') }}：</span>
          <span class="text-[var(--sp-text-secondary)] truncate">{{ acc.raw.status_checked_at ? formatDateTime(acc.raw.status_checked_at) : t('accounts.neverChecked') }}</span>
        </div>
      </div>

      <!-- Actions：竖排布局保留，语义走 ui-row-action -->
      <div class="mt-auto pt-3 border-t border-[var(--sp-border)] grid grid-cols-8 gap-0.5">
        <button type="button" class="ui-row-action ui-row-action--stack" :disabled="checkingAccount === acc.name" :title="t('accounts.checkStatus')" @click="handleCheck(acc.name)">
          <span v-if="checkingAccount === acc.name" class="ui-spinner !w-3.5 !h-3.5 !border-2" />
          <Play v-else class="w-3.5 h-3.5" />
          <span>{{ t('accounts.check') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.chat')" @click="goChats(acc.name)">
          <MessagesSquare class="w-3.5 h-3.5" />
          <span>{{ t('accounts.chat') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.viewTasks')" @click="goTasks(acc.name)">
          <Zap class="w-3.5 h-3.5" />
          <span>{{ t('accounts.tasks') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.viewLogs')" @click="goLogs(acc.name)">
          <FileText class="w-3.5 h-3.5" />
          <span>{{ t('accounts.logs') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.devices')" @click="openDevices(acc.name)">
          <MonitorSmartphone class="w-3.5 h-3.5" />
          <span>{{ t('accounts.devicesShort') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.officialMessages')" @click="openOfficialMessages(acc.name)">
          <MessageCircle class="w-3.5 h-3.5" />
          <span>{{ t('accounts.officialMessagesShort') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack" :title="t('accounts.edit')" @click="openEdit(acc)">
          <Edit2 class="w-3.5 h-3.5" />
          <span>{{ t('accounts.editBtn') }}</span>
        </button>
        <button type="button" class="ui-row-action ui-row-action--stack ui-row-action--danger" :title="t('accounts.deleteBtn')" @click="handleDelete(acc.name)">
          <Trash2 class="w-3.5 h-3.5" />
          <span>{{ t('accounts.deleteBtn') }}</span>
        </button>
      </div>
    </div>
    </div>
    </div>

    <!-- FAB for Adding Account -->
    <div class="fixed ui-safe-fab z-40 flex flex-col items-end gap-2">
      <transition enter-active-class="transition duration-200 ease-out" enter-from-class="opacity-0 translate-y-2" enter-to-class="opacity-100 translate-y-0" leave-active-class="transition duration-150 ease-in" leave-from-class="opacity-100 translate-y-0" leave-to-class="opacity-0 translate-y-2">
        <div v-if="showAddMenu" class="flex flex-col gap-1.5 mb-1">
          <button type="button" class="ui-card ui-card-hover flex items-center gap-2.5 px-4 py-2.5 text-sm text-[var(--sp-text)] shadow-[var(--sp-shadow-md)] rounded-[var(--sp-radius)] cursor-pointer" @click="openAddModal('qr')">
            <QrCode class="w-4 h-4 text-[var(--sp-text-muted)]" /> {{ t('accounts.qrLogin') }}
          </button>
          <button type="button" class="ui-card ui-card-hover flex items-center gap-2.5 px-4 py-2.5 text-sm text-[var(--sp-text)] shadow-[var(--sp-shadow-md)] rounded-[var(--sp-radius)] cursor-pointer" @click="openAddModal('code')">
            <Phone class="w-4 h-4 text-[var(--sp-text-muted)]" /> {{ t('accounts.codeLogin') }}
          </button>
          <button type="button" class="ui-card ui-card-hover flex items-center gap-2.5 px-4 py-2.5 text-sm text-[var(--sp-text)] shadow-[var(--sp-shadow-md)] rounded-[var(--sp-radius)] cursor-pointer" @click="openAddModal('import')">
            <FileDown class="w-4 h-4 text-[var(--sp-text-muted)]" /> {{ t('accounts.importLogin') }}
          </button>
          <button type="button" class="ui-card ui-card-hover flex items-center gap-2.5 px-4 py-2.5 text-sm text-[var(--sp-text)] shadow-[var(--sp-shadow-md)] rounded-[var(--sp-radius)] cursor-pointer" @click="openAddModal('passkey')">
            <KeyRound class="w-4 h-4 text-[var(--sp-text-muted)]" /> {{ t('accounts.passkeyLogin') }}
          </button>
        </div>
      </transition>
      
      <button 
        type="button"
        class="ui-fab cursor-pointer"
        :aria-expanded="showAddMenu"
        :aria-label="showAddMenu ? t('common.close') : t('accounts.addAccount')"
        :title="showAddMenu ? t('common.close') : t('accounts.addAccount')"
        @click="showAddMenu = !showAddMenu"
      >
        <Plus class="w-5 h-5 transition-transform duration-200" :class="{ 'rotate-45': showAddMenu }" />
      </button>
    </div>

    <!-- Modals -->
    <AccountImpactModal :isOpen="!!changePlan" :plan="changePlan" :loading="changeLoading" :error="changeError" @close="changePlan = null" @apply="applyDelete" />
    <AccountTagsModal :isOpen="showTagsModal" :account="tagsAccount" :mode="tagsMode" @close="showTagsModal = false" @success="loadAccounts" />
    <AddAccountModal :isOpen="showAddModal" :initialMethod="initialMethod" :initialAccountName="initialAccountName" @close="showAddModal = false" @success="loadAccounts" />
    <EditAccountModal v-if="editingAccount" :isOpen="showEditModal" :account="editingAccount" @close="showEditModal = false" @closed="onEditModalClosed" @success="loadAccounts" @relogin="handleRelogin" />
    <DeviceManagerModal :isOpen="showDeviceModal" :accountName="deviceAccountName" @close="showDeviceModal = false" />
    <SessionPackageModal :isOpen="showPackageModal" :accountName="packageAccountName" @close="showPackageModal = false" />
    <OfficialMessagesModal :isOpen="showOfficialMessagesModal" :accountName="officialMessagesAccountName" @close="showOfficialMessagesModal = false" />
  </div>
</template>
