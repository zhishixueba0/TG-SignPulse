import { describe, it, expect } from 'vitest'
import {
  canMoveAccount,
  collectAccountDcIds,
  compareAccountNames,
  filterAccountsByDc,
  filterAccountsByQuery,
  filterAccountsByStatus,
  mapAccountInfoToUiItem,
  sortAccounts,
} from '../lib/account-list-map'
import type { AccountInfo } from '../lib/api'

const labels = { loginExpired: '登录失效', checking: '检测中' }

describe('account-list-map', () => {
  it('maps invalid and checking statuses', () => {
    const invalid = mapAccountInfoToUiItem(
      { name: 'a', needs_relogin: true, status: 'invalid' } as AccountInfo,
      labels,
    )
    expect(invalid.status).toBe('error')
    expect(invalid.message).toBe('登录失效')

    const checking = mapAccountInfoToUiItem(
      { name: 'b', status: 'checking' } as AccountInfo,
      labels,
    )
    expect(checking.status).toBe('empty')
    expect(checking.message).toBe('检测中')
  })

  it('filters by name remark message', () => {
    const list = [
      { name: 'alice', remark: '主号', message: '' },
      { name: 'bob', remark: '', message: '额度不足' },
    ]
    expect(filterAccountsByQuery(list, '主')).toHaveLength(1)
    expect(filterAccountsByQuery(list, '额度')).toHaveLength(1)
    expect(filterAccountsByQuery(list, '')).toHaveLength(2)
  })

  it("sorts account names with natural numeric order", () => {
    const names = ["11", "1", "9", "账号11", "账号9", "账号1"]
    names.sort(compareAccountNames)
    expect(names).toEqual(["1", "9", "11", "账号1", "账号9", "账号11"])
  })
})

describe('account sort / filter (Batch 1)', () => {
  const mk = (
    name: string,
    status: string,
    checkedAt?: string | null,
    dcId?: number | null,
  ) => ({
    name,
    status,
    raw: { status_checked_at: checkedAt ?? null, dc_id: dcId ?? null },
  })

  it('custom order keeps the server order', () => {
    const list = [mk('b', 'active'), mk('a', 'active'), mk('c', 'error')]
    expect(sortAccounts(list, 'custom').map((a) => a.name)).toEqual(['b', 'a', 'c'])
  })

  it('abnormal first: error, then empty, then active, stable within each tier', () => {
    const list = [
      mk('a1', 'active'),
      mk('e1', 'error'),
      mk('m1', 'empty'),
      mk('a2', 'active'),
      mk('e2', 'error'),
    ]
    expect(sortAccounts(list, 'abnormal').map((a) => a.name)).toEqual([
      'e1',
      'e2',
      'm1',
      'a1',
      'a2',
    ])
  })

  it('oldest checked first: never checked first, then ascending checked_at', () => {
    const list = [
      mk('new', 'active', '2026-11-05T10:00:00'),
      mk('never', 'active', null),
      mk('old', 'active', '2026-11-01T10:00:00'),
      mk('mid', 'active', '2026-11-03T10:00:00'),
    ]
    expect(sortAccounts(list, 'oldestChecked').map((a) => a.name)).toEqual([
      'never',
      'old',
      'mid',
      'new',
    ])
  })

  it('name sort uses natural numeric order', () => {
    const list = [mk('账号11', 'active'), mk('账号9', 'active'), mk('账号1', 'active')]
    expect(sortAccounts(list, 'name').map((a) => a.name)).toEqual([
      '账号1',
      '账号9',
      '账号11',
    ])
  })

  it('status filter: available is active only, offline is error only', () => {
    const list = [mk('a', 'active'), mk('e', 'error'), mk('m', 'empty')]
    expect(filterAccountsByStatus(list, 'all')).toHaveLength(3)
    expect(filterAccountsByStatus(list, 'available').map((a) => a.name)).toEqual(['a'])
    expect(filterAccountsByStatus(list, 'offline').map((a) => a.name)).toEqual(['e'])
    // empty（检测中/额度提示）既不是可用也不是离线，只在全部里出现
    expect(filterAccountsByStatus(list, 'available')).not.toContainEqual(
      expect.objectContaining({ name: 'm' }),
    )
  })

  it('dc options are collected deduped and sorted; dc filter matches exactly', () => {
    const list = [
      mk('a', 'active', null, 5),
      mk('b', 'active', null, 1),
      mk('c', 'active', null, 5),
      mk('d', 'active', null, null),
    ]
    expect(collectAccountDcIds(list)).toEqual([1, 5])
    expect(filterAccountsByDc(list, null)).toHaveLength(4)
    expect(filterAccountsByDc(list, 5).map((a) => a.name)).toEqual(['a', 'c'])
    expect(filterAccountsByDc(list, 2)).toHaveLength(0)
  })

  it('move bounds: first cannot move up, last cannot move down', () => {
    expect(canMoveAccount(0, 3)).toEqual({ canMoveUp: false, canMoveDown: true })
    expect(canMoveAccount(1, 3)).toEqual({ canMoveUp: true, canMoveDown: true })
    expect(canMoveAccount(2, 3)).toEqual({ canMoveUp: true, canMoveDown: false })
    expect(canMoveAccount(0, 1)).toEqual({ canMoveUp: false, canMoveDown: false })
  })
})
