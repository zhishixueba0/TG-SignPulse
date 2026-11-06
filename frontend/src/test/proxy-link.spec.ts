import { describe, expect, it, vi, beforeEach } from 'vitest'
import * as coreApi from '../lib/api/core'
import { getProxyLink } from '../lib/api/proxies'

describe('proxies api 复制链接', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getProxyLink 发起 GET 请求并携带 token，返回节点链接', async () => {
    const mockResp = { link: 'tg://proxy?server=1.2.3.4&port=443&secret=dd00' }
    const requestSpy = vi.spyOn(coreApi, 'request').mockResolvedValueOnce(mockResp)

    const result = await getProxyLink('test-token', 'abc123')
    expect(requestSpy).toHaveBeenCalledWith('/proxies/abc123/link', {}, 'test-token')
    expect(result).toEqual(mockResp)
  })

  it('getProxyLink 对 id 做 URL 编码', async () => {
    const requestSpy = vi.spyOn(coreApi, 'request').mockResolvedValueOnce({ link: 'http://1.2.3.4:8080' })

    await getProxyLink('test-token', 'a/b c')
    expect(requestSpy).toHaveBeenCalledWith('/proxies/a%2Fb%20c/link', {}, 'test-token')
  })
})
