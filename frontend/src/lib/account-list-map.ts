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
