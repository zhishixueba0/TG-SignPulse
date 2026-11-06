import { describe, expect, it } from 'vitest'
import { formatEstMonth, registrationDisplay } from '../lib/registration-estimate'

const t = (key: string): string => {
  const dict: Record<string, string> = {
    'accounts.registeredUnknown': '未知',
    'accounts.registeredEstPrefix': '约',
    'accounts.registeredAfter': '之后',
    'accounts.registeredBefore': '之前',
    'accounts.registeredEstTitleUserId': '根据账号 ID 估算，仅供参考',
    'accounts.registeredEstTitleBound': '账号 ID 超出对照表范围，仅能给出边界月份',
  }
  return dict[key] ?? key
}

describe('注册日期第一步：前端显示', () => {
  it('formatEstMonth 只到月，不显示日', () => {
    expect(formatEstMonth('2022-05', false)).toBe('2022年5月')
    expect(formatEstMonth('2022-05', true)).toBe('May 2022')
    expect(formatEstMonth('2013-08', false)).toBe('2013年8月')
    expect(formatEstMonth('2024-12', true)).toBe('December 2024')
    // 非法输入原样返回，不崩
    expect(formatEstMonth('not-a-month', false)).toBe('not-a-month')
  })

  it('表内估计带“约”', () => {
    const d = registrationDisplay(
      { registered_est_month: '2022-05', registered_est_source: 'user_id' },
      t,
      false,
    )
    expect(d.text).toBe('约2022年5月')
    expect(d.title).toBe('根据账号 ID 估算，仅供参考')
  })

  it('表外新账号只给“之后”，不编假月份', () => {
    const d = registrationDisplay(
      { registered_est_month: '2024-12', registered_est_source: 'bound_after' },
      t,
      false,
    )
    expect(d.text).toBe('2024年12月之后')
    expect(d.title).toBe('账号 ID 超出对照表范围，仅能给出边界月份')
  })

  it('表外老账号只给“之前”', () => {
    const d = registrationDisplay(
      { registered_est_month: '2013-08', registered_est_source: 'bound_before' },
      t,
      false,
    )
    expect(d.text).toBe('2013年8月之前')
  })

  it('无数据为“未知”', () => {
    const d = registrationDisplay(
      { registered_est_month: null, registered_est_source: null },
      t,
      false,
    )
    expect(d.text).toBe('未知')
    expect(d.title).toBe('')
  })
})
