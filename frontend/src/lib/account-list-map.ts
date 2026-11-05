/**
 * 账号列表：API → UI 映射与本地筛选（纯函数）。
 */
import type { AccountInfo } from './api'
import type { AccountUiItem } from './types'

export type AccountMapLabels = {
  loginExpired: string
  checking: string
}

/** 服务端账号 → 列表行字段（不含头像 blob） */
export function mapAccountInfoToUiItem(
  acc: AccountInfo,
  labels: AccountMapLabels,
): AccountUiItem {
  let uiStatus = 'active'
  let message = ''

  if (acc.needs_relogin || acc.status === 'invalid') {
    uiStatus = 'error'
    message = labels.loginExpired
  } else if (acc.status === 'error') {
    uiStatus = 'error'
    message = acc.status_message || ''
  } else if (acc.status === 'checking') {
    uiStatus = 'empty'
    message = labels.checking
  } else if (
    acc.status_message?.includes('流量') ||
    acc.status_message?.includes('额度')
  ) {
    uiStatus = 'empty'
    message = acc.status_message
  }

  return {
    id: acc.name,
    name: acc.name,
    remark: acc.remark,
    status: uiStatus,
    message,
    avatarUrl: '',
    raw: acc,
  }
}

export function filterAccountsByQuery<
  T extends {
    name: string
    remark?: string | null
    message?: string | null
    raw?: { username?: string | null; phone?: string | null }
  },
>(accounts: T[], searchQuery: string): T[] {
  const q = searchQuery.trim().toLowerCase()
  if (!q) return accounts
  return accounts.filter(
    (a) =>
      a.name.toLowerCase().includes(q) ||
      (a.remark || '').toLowerCase().includes(q) ||
      (a.message || '').toLowerCase().includes(q) ||
      (a.raw?.username || '').toLowerCase().includes(q) ||
      (a.raw?.phone || '').toLowerCase().includes(q),
  )
}

/**
 * 手机号脱敏：保留 +<国家码> 与末 2 位，中间打码。
 * 例：+1 9295550102 → +1 929***-**02；无 + 前缀时保留前 3 位与末 2 位。
 */
export function maskPhone(phone: string): string {
  const digits = phone.replace(/[^\d]/g, '')
  if (digits.length <= 5) return phone
  if (phone.trim().startsWith('+')) {
    // 国家码长度 = 总长 - 10（默认按 10 位本地号码计，不足时取 1 位）
    const ccLen = digits.length > 10 ? digits.length - 10 : 1
    const rest = digits.slice(ccLen)
    return `+${digits.slice(0, ccLen)} ${rest.slice(0, 3)}***-**${rest.slice(-2)}`
  }
  return `${digits.slice(0, 3)}***-**${digits.slice(-2)}`
}

/** 用户 ID 脱敏：保留前 3 位与后 3 位，中间打码。例：770123960 → 770***960 */
export function maskUserId(userId: number | string): string {
  const s = String(userId)
  if (s.length <= 6) return s
  return `${s.slice(0, 3)}***${s.slice(-3)}`
}

export function compareAccountNames(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })
}

/** 账号列表排序键：custom 为服务端返回的原始顺序（即 sort_order 持久化顺序） */
export type AccountSortKey = 'custom' | 'abnormal' | 'oldestChecked' | 'name'

/** 账号列表状态筛选：受限（SpamBot）目前分不出来，见 GAP-ACCOUNTS.md */
export type AccountStatusFilter = 'all' | 'available' | 'offline'

export interface SortableAccount {
  name: string
  status?: string
  raw?: { status_checked_at?: string | null; dc_id?: number | null }
}

/** 异常分层：error 最前，empty（检测中/额度提示）其次，active 最后 */
function statusTier(status: string | undefined): number {
  if (status === 'error') return 0
  if (status === 'empty') return 1
  return 2
}

/** 检测时间比较：从未检测的排最前，其余按 status_checked_at 升序（ISO 可直接比字典序） */
function compareCheckedAt(
  a: string | null | undefined,
  b: string | null | undefined,
): number {
  if (!a && !b) return 0
  if (!a) return -1
  if (!b) return 1
  if (a === b) return 0
  return a < b ? -1 : 1
}

/** 稳定排序：比较器相等时保持原顺序 */
function stableSort<T>(items: T[], cmp: (a: T, b: T) => number): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => cmp(a.item, b.item) || a.index - b.index)
    .map(({ item }) => item)
}

export function sortAccounts<T extends SortableAccount>(
  accounts: T[],
  sortKey: AccountSortKey,
): T[] {
  switch (sortKey) {
    case 'name':
      return stableSort(accounts, (a, b) => compareAccountNames(a.name, b.name))
    case 'abnormal':
      return stableSort(
        accounts,
        (a, b) => statusTier(a.status) - statusTier(b.status),
      )
    case 'oldestChecked':
      return stableSort(
        accounts,
        (a, b) => compareCheckedAt(a.raw?.status_checked_at, b.raw?.status_checked_at),
      )
    case 'custom':
    default:
      return accounts
  }
}

/**
 * 按状态筛选：available = 会话可用（uiStatus active），offline = 会话失效/报错
 * （uiStatus error，需重新登录）。empty（检测中/额度提示）只在全部里出现，
 * 因为它既不是可用也不是离线——受限的准确判定要等 SpamBot 分类（Batch 3）。
 */
export function filterAccountsByStatus<T extends { status?: string }>(
  accounts: T[],
  filter: AccountStatusFilter,
): T[] {
  if (filter === 'available') return accounts.filter((a) => a.status === 'active')
  if (filter === 'offline') return accounts.filter((a) => a.status === 'error')
  return accounts
}

/** 收集实际出现的 dc_id（去重、升序），供机房筛选下拉动态生成选项 */
export function collectAccountDcIds<T extends { raw?: { dc_id?: number | null } }>(
  accounts: T[],
): number[] {
  const ids = new Set<number>()
  for (const a of accounts) {
    if (typeof a.raw?.dc_id === 'number') ids.add(a.raw.dc_id)
  }
  return [...ids].sort((x, y) => x - y)
}

/**
 * 上移/下移可用态：按完整自定义顺序（服务端返回顺序）里的下标判定，
 * 与列表筛选/排序下拉无关——筛选后隐藏的邻居仍参与顺序交换。
 */
export function canMoveAccount(index: number, total: number): {
  canMoveUp: boolean
  canMoveDown: boolean
} {
  return {
    canMoveUp: index > 0,
    canMoveDown: index >= 0 && index < total - 1,
  }
}

export function filterAccountsByDc<T extends { raw?: { dc_id?: number | null } }>(
  accounts: T[],
  dcId: number | null,
): T[] {
  if (dcId === null) return accounts
  return accounts.filter((a) => a.raw?.dc_id === dcId)
}
