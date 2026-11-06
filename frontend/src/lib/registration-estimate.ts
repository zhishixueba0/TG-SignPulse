import type { AccountInfo } from './api/accounts'

const MONTH_NAMES_EN = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

/** 注册日期第一步：把后端 "YYYY-MM" 格式化为本地化的年月（只到月，不显示日）。 */
export function formatEstMonth(ym: string, isEn: boolean): string {
  const [y, m] = ym.split('-')
  const monthNum = Number(m)
  if (!y || !monthNum || monthNum < 1 || monthNum > 12) return ym
  return isEn ? `${MONTH_NAMES_EN[monthNum - 1]} ${y}` : `${y}年${monthNum}月`
}

export interface RegistrationDisplay {
  text: string
  title: string
}

/**
 * 注册日期第一步：卡片注册行文案。
 * 表内估计带“约”；表外只给“之后/之前”（绝不把钳制值当估计月）；无数据为“未知”。
 */
export function registrationDisplay(
  raw: Pick<AccountInfo, 'registered_est_month' | 'registered_est_source'>,
  t: (key: string) => string,
  isEn: boolean,
): RegistrationDisplay {
  const month = raw.registered_est_month
  if (!month) return { text: t('accounts.registeredUnknown'), title: '' }
  const formatted = formatEstMonth(month, isEn)
  if (raw.registered_est_source === 'bound_after') {
    return {
      text: `${formatted}${t('accounts.registeredAfter')}`,
      title: t('accounts.registeredEstTitleBound'),
    }
  }
  if (raw.registered_est_source === 'bound_before') {
    return {
      text: `${formatted}${t('accounts.registeredBefore')}`,
      title: t('accounts.registeredEstTitleBound'),
    }
  }
  return {
    text: `${t('accounts.registeredEstPrefix')}${formatted}`,
    title: t('accounts.registeredEstTitleUserId'),
  }
}
