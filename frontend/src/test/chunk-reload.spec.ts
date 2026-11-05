import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

async function loadModule() {
  vi.resetModules()
  return (await import('../lib/chunk-reload')) as typeof import('../lib/chunk-reload')
}

describe('isChunkLoadError', () => {
  it('recognizes Vite dynamic import failures', async () => {
    const { isChunkLoadError } = await loadModule()
    expect(
      isChunkLoadError(
        new Error(
          'Failed to fetch dynamically imported module: http://127.0.0.1:18766/assets/Chats-abc.js',
        ),
      ),
    ).toBe(true)
    expect(isChunkLoadError(new Error('Loading chunk 42 failed'))).toBe(true)
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true)
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(true)
  })

  it('ignores unrelated errors', async () => {
    const { isChunkLoadError } = await loadModule()
    expect(isChunkLoadError(new Error('ACCOUNT_BUSY'))).toBe(false)
    expect(isChunkLoadError(new Error('Network Error'))).toBe(false)
    expect(isChunkLoadError(new Error('Request failed with status code 404'))).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
  })
})

describe('reloadOnceForChunkError', () => {
  let reloadSpy: ReturnType<typeof vi.fn>
  const originalLocation = window.location

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-06T06:00:00Z'))
    sessionStorage.clear()
    reloadSpy = vi.fn()
    // jsdom 的 location.reload 不可 spy，整个替换 location 对象。
    Object.defineProperty(window, 'location', {
      value: { reload: reloadSpy },
      writable: true,
      configurable: true,
    })
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      value: originalLocation,
      writable: true,
      configurable: true,
    })
    vi.restoreAllMocks()
    vi.useRealTimers()
    sessionStorage.clear()
  })

  it('reloads once and blocks a second reload on the same page', async () => {
    const { reloadOnceForChunkError } = await loadModule()
    expect(reloadOnceForChunkError()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
    expect(reloadOnceForChunkError()).toBe(false)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('blocks reload again within 60s after a reload (fresh page load)', async () => {
    const first = await loadModule()
    expect(first.reloadOnceForChunkError()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(1)

    // 模拟刷新后的新页面：模块重新加载，sessionStorage 仍留着上次的时间戳。
    vi.advanceTimersByTime(30_000)
    const second = await loadModule()
    expect(second.reloadOnceForChunkError()).toBe(false)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
  })

  it('allows reload again after the 60s cooldown', async () => {
    const first = await loadModule()
    expect(first.reloadOnceForChunkError()).toBe(true)

    vi.advanceTimersByTime(61_000)
    const second = await loadModule()
    expect(second.reloadOnceForChunkError()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(2)
  })
})
