/**
 * 复现：从聊天中心切到其他页面后内容区空白（James 2026-11-06 汇报）。
 * 不进聊天中心时页面间切换正常；只有进过聊天中心后切页面才空白，
 * 且侧边栏/顶栏正常、只有 router-view 子页面空白。
 * 用真实 Layout + 真实 router 做导航复现：Dashboard → Chats → Accounts，
 * 对照组 Dashboard → Accounts（不进 Chats）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import router from '../router'
import i18n from '../i18n'
import { useAuthStore } from '../stores/auth'
import LayoutView from '../views/Layout.vue'

vi.mock('../lib/api/core', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api/core')>()
  return {
    ...actual,
    withToken: vi.fn((cb) => cb('mock-token')),
    getAuthToken: vi.fn(() => 'mock-token'),
    request: vi.fn().mockResolvedValue({}),
  }
})

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  // mock 数据必须写在 factory 内：vi.mock 会被提升，不能引用文件顶层变量。
  const mockAccount = {
    name: '12098814721',
    session_file: '12098814721.session',
    exists: true,
    size: 1024,
    status: 'connected',
    user_id: 8789956089,
    dc_id: 1,
    dc_location: '美国迈阿密',
    phone: '12098814721',
    username: 'Nora_Hutton',
    first_name: 'Nora',
    proxy_mode: 'direct',
    added_at: '2026-11-05T14:25:00+08:00',
  }
  const mockDialog = {
    chat_id: 178220800,
    title: 'Spam Info Bot',
    type: 'bot',
    username: 'SpamBot',
    unread_count: 1,
    last_message_text: 'Choose:',
    last_message_date: '2026-11-06T07:01:00Z',
  }
  const mockMessages = [
    {
      id: 1,
      date: '2026-11-06T07:00:00Z',
      text: 'Hello',
      outgoing: false,
      sender_name: 'Spam Info Bot',
      sender_id: 178220800,
      media_type: null,
      keyboard_type: null,
      buttons: [],
    },
    {
      id: 2,
      date: '2026-11-06T07:01:00Z',
      text: 'Choose:',
      outgoing: false,
      sender_name: 'Spam Info Bot',
      sender_id: 178220800,
      media_type: null,
      keyboard_type: 'reply',
      buttons: [
        { row: 0, col: 0, text: 'OK', kind: 'reply', url: null },
        { row: 1, col: 0, text: 'What is spam?', kind: 'reply', url: null },
      ],
    },
  ]
  return {
    ...actual,
    getAppVersion: vi.fn().mockResolvedValue({ version: '9.9.9' }),
    listAccounts: vi.fn().mockResolvedValue({ accounts: [mockAccount], total: 1 }),
    listChatDialogs: vi.fn().mockResolvedValue({ dialogs: [mockDialog], total: 1 }),
    listChatMessages: vi.fn().mockResolvedValue({ messages: mockMessages, total: 2 }),
    fetchAccountAvatar: vi.fn().mockResolvedValue(null),
    getHistoryTrends: vi.fn().mockResolvedValue({
      days: 7,
      total_runs: 0,
      total_success: 0,
      total_failed: 0,
      overall_success_rate: 1,
      trends: [],
      categories: {},
    }),
  }
})

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

async function settle(ms = 300) {
  await flushPromises()
  await nextTick()
  await sleep(ms)
  await flushPromises()
  await nextTick()
}

describe('聊天中心 → 其他页面导航', () => {
  // 真实挂载 + 多次导航 + 定时等待较慢，全量跑时易超默认 5 秒，单独放宽超时。
  const vueErrors: Array<{ message: string; stack?: string }> = []

  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vueErrors.length = 0

    const authStore = useAuthStore()
    const payload = btoa(JSON.stringify({ exp: 4956508800 }))
    authStore.setToken(`header.${payload}.signature`)

    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia
  })

  function mountLayout() {
    return mount(LayoutView, {
      global: {
        plugins: [i18n, router],
        config: {
          errorHandler: (err) => {
            vueErrors.push({
              message: err instanceof Error ? err.message : String(err),
              stack: err instanceof Error ? err.stack : undefined,
            })
          },
        },
      },
    })
  }

  /** 内容区（router-view）是否渲染出目标页面的标志内容 */
  function contentText(wrapper: ReturnType<typeof mount>) {
    const main = wrapper.find('main')
    return main.exists() ? main.text() : ''
  }

  it('对照组：Dashboard → Accounts 直接切换，账号卡应渲染', async () => {
    const wrapper = mountLayout()
    await router.push('/dashboard')
    await settle()
    await router.push('/accounts')
    await settle()
    expect(router.currentRoute.value.name).toBe('accounts')
    // 手机号默认掩码，用卡片上的名字/总数做标志
    expect(contentText(wrapper)).toContain('Nora')
    expect(contentText(wrapper)).toContain('账号总数：1')
    expect(vueErrors).toEqual([])
    wrapper.unmount()
  })

  it('对照组：直接进 Dashboard，不应有渲染错误', async () => {
    const wrapper = mountLayout()
    await router.push('/dashboard')
    await settle()
    expect(router.currentRoute.value.name).toBe('dashboard')
    expect(contentText(wrapper).length).toBeGreaterThan(50)
    expect(vueErrors).toEqual([])
    wrapper.unmount()
  })

  it('复现：Dashboard → Chats（加载对话与消息）→ Accounts，账号卡应渲染', async () => {
    const wrapper = mountLayout()
    await router.push('/dashboard')
    await settle()

    await router.push('/chats')
    await settle()
    expect(router.currentRoute.value.name).toBe('chats')
    // 对话列表应出现 SpamBot
    expect(contentText(wrapper)).toContain('Spam Info Bot')

    // 点开 SpamBot 对话，加载消息（含回复键盘）
    const dialogButtons = wrapper.findAll('button').filter((b) => b.text().includes('Spam Info Bot'))
    expect(dialogButtons.length).toBeGreaterThan(0)
    await dialogButtons[0].trigger('click')
    await settle()
    expect(contentText(wrapper)).toContain('Choose:')

    // 切到账号管理
    await router.push('/accounts')
    await settle()
    expect(router.currentRoute.value.name).toBe('accounts')
    expect(contentText(wrapper)).toContain('Nora')
    expect(contentText(wrapper)).toContain('账号总数：1')
    expect(vueErrors).toEqual([])
    wrapper.unmount()
  })

  it('复现：Chats → Dashboard 也应渲染', async () => {
    const wrapper = mountLayout()
    await router.push('/chats')
    await settle()
    await router.push('/dashboard')
    await settle()
    expect(router.currentRoute.value.name).toBe('dashboard')
    expect(contentText(wrapper).length).toBeGreaterThan(50)
    expect(vueErrors).toEqual([])
    wrapper.unmount()
  })

  it('错误边界：子页面渲染抛错时显示错误卡片（不是整片空白），重试后恢复', async () => {
    // 趋势接口返回一条 null 数据，Dashboard 趋势图在渲染期抛错。
    const { getHistoryTrends } = await import('../lib/api')
    vi.mocked(getHistoryTrends).mockResolvedValueOnce({
      days: 7,
      total_runs: 0,
      total_success: 0,
      total_failed: 0,
      overall_success_rate: 1,
      trends: [null],
      categories: {},
    } as never)
    const wrapper = mountLayout()
    await router.push('/dashboard')
    await settle()
    // 渲染失败不应静默空白：应出现错误卡片（带原文案与重试按钮）
    expect(contentText(wrapper)).toContain('页面渲染失败')
    expect(contentText(wrapper)).toContain('重试')
    // 错误已被 Layout 边界接住，不应冒泡到全局 handler
    expect(vueErrors).toEqual([])
    // 重试：再次加载走默认 mock（空趋势），应恢复渲染
    const retryBtn = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    expect(retryBtn).toBeTruthy()
    await retryBtn!.trigger('click')
    await settle()
    expect(contentText(wrapper)).not.toContain('页面渲染失败')
    expect(contentText(wrapper)).toContain('签到执行与成功率趋势')
    wrapper.unmount()
  })
}, 20000)
