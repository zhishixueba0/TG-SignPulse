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

  it('池代理下拉展示自定义名 · 接入 IP · 协议 · ASN 机构，不含链接', () => {
    const ipItem = {
      id: 'x',
      label: '法国-1',
      ptype: 'http',
      host: '1.2.3.4',
      resolved_ip: '1.2.3.4',
      org: 'Example Org',
      country_code: 'FR',
    } as ProxyItem
    expect(poolProxyOptionLabel(ipItem)).toBe('法国-1 · 1.2.3.4 · HTTP · Example Org')
    // 域名接入显示解析出的 IP，不显示域名与链接
    const domainItem = {
      id: 'y',
      label: '德国-1',
      ptype: 'mtproto',
      host: 'example.com',
      resolved_ip: '5.6.7.8',
      org: 'Hetzner Online GmbH',
      country_code: 'DE',
    } as ProxyItem
    expect(poolProxyOptionLabel(domainItem)).toBe(
      '德国-1 · 5.6.7.8 · MTPROTO · Hetzner Online GmbH',
    )
    // 无机构时不显示这一段；无解析 IP 时退回 host
    const noOrg = { ...ipItem, org: null, isp: null, announced_asn: null } as ProxyItem
    expect(poolProxyOptionLabel(noOrg)).toBe('法国-1 · 1.2.3.4 · HTTP')
    const unresolved = { ...domainItem, resolved_ip: null } as ProxyItem
    expect(poolProxyOptionLabel(unresolved)).toBe(
      '德国-1 · example.com · MTPROTO · Hetzner Online GmbH',
    )
  })
})
