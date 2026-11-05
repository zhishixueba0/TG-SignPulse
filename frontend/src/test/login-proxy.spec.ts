import { describe, it, expect } from 'vitest'
import { loginProxyPayload, poolProxyOptionLabel } from '../lib/login-proxy'
import type { ProxyItem } from '../lib/api/proxies'

describe('login-proxy', () => {
  it('pool 只传 proxy_id，不传真实 URL', () => {
    expect(loginProxyPayload('pool', 'abc123', '')).toEqual({
      proxy_id: 'abc123',
      proxy_mode: 'pool',
    })
  })

  it('custom 传自由文本 URL', () => {
    expect(loginProxyPayload('custom', '', 'socks5://127.0.0.1:1080')).toEqual({
      proxy: 'socks5://127.0.0.1:1080',
      proxy_mode: 'custom',
    })
    expect(loginProxyPayload('custom', '', '  ')).toEqual({
      proxy: undefined,
      proxy_mode: 'custom',
    })
  })

  it('inherit / direct 只传 mode', () => {
    expect(loginProxyPayload('inherit', '', '')).toEqual({ proxy_mode: 'inherit' })
    expect(loginProxyPayload('direct', '', '')).toEqual({ proxy_mode: 'direct' })
  })

  it('池代理下拉展示掩码地址与地区，不含凭据', () => {
    const item = {
      id: 'x',
      label: '法国-1',
      display_url: 'http://1.2.3.4:8080',
      country_code: 'FR',
    } as ProxyItem
    expect(poolProxyOptionLabel(item)).toBe('法国-1 · http://1.2.3.4:8080 (FR)')
    const noRegion = { ...item, country_code: null } as ProxyItem
    expect(poolProxyOptionLabel(noRegion)).toBe('法国-1 · http://1.2.3.4:8080')
  })
})
