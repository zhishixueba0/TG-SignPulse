/**
 * 加账号登录代理选择：四种登录入口共用的纯函数。
 * 模式与编辑账号弹窗（EditAccountModal）一致：继承全局 / 直连 /
 * 指定池代理 / 自定义 URL。选了池代理只传 proxy_id（真实 URL 不出
 * 后端）；选了不存在的代理后端返回 404，绝不静默直连。
 */
import type { ProxyItem } from './api/proxies'

export type LoginProxyMode = 'inherit' | 'direct' | 'pool' | 'custom'

export interface LoginProxyPayload {
  proxy?: string
  proxy_id?: string
  proxy_mode: LoginProxyMode
}

/** 按当前选择拼出四个登录接口共用的代理字段。 */
export function loginProxyPayload(
  mode: LoginProxyMode,
  proxyId: string,
  proxyUrl: string,
): LoginProxyPayload {
  if (mode === 'pool') {
    return { proxy_id: proxyId || undefined, proxy_mode: 'pool' }
  }
  if (mode === 'custom') {
    const url = proxyUrl.trim()
    return { proxy: url || undefined, proxy_mode: 'custom' }
  }
  return { proxy_mode: mode }
}

/** 池代理下拉展示：名称 · 掩码地址（地区）。不含任何凭据。 */
export function poolProxyOptionLabel(item: ProxyItem): string {
  const region = item.country_code ? ` (${item.country_code})` : ''
  return `${item.label} · ${item.display_url}${region}`
}
