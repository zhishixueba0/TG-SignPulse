import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import SpamCheckModal from '../components/accounts/SpamCheckModal.vue'
import * as accountsApi from '../lib/api/accounts'
import * as coreApi from '../lib/api/core'

vi.mock('../composables/useI18n', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

// Modal 用最小替身，只渲染默认插槽与 footer
const ModalStub = defineComponent({
  name: 'Modal',
  emits: ['close', 'closed'],
  template: `<div><slot /><slot name="footer" /></div>`,
})

const freeResult = {
  success: true,
  account_name: 'acc-a',
  status: 'free',
  bidirectional: 'both',
  bidirectional_label: '正常',
  summary: '正常：无限制',
  message: "Good news, no limits are currently applied to your account. You're free as a bird!",
  until: null,
  checked_at: '2026-11-06T06:00:00Z',
}

const limitedResult = {
  ...freeResult,
  status: 'limited',
  bidirectional: 'partial',
  bidirectional_label: '部分双向',
  summary: '部分双向：只能给特定区域的用户随意互发',
  message: 'Unfortunately, some actions on your account are limited.',
}

async function mountModal(
  previous: {
    status?: string | null
    checked_at?: string | null
    summary?: string | null
  } | null = null,
) {
  const wrapper = mount(SpamCheckModal, {
    props: { isOpen: true, accountName: 'acc-a', previous },
    global: { stubs: { Modal: ModalStub } },
  })
  await flushPromises()
  return wrapper
}

describe('SpamCheckModal 双向检测', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(coreApi, 'getAuthToken').mockReturnValue('mock-token')
  })

  it('初始显示说明与开始检测按钮', async () => {
    const wrapper = await mountModal()
    expect(wrapper.text()).toContain('spamCheck.intro')
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('spamCheck.start'))
    expect(startBtn).toBeTruthy()
  })

  it('点开始检测后调接口，free 结果显示没有受到限制与原文', async () => {
    vi.spyOn(accountsApi, 'checkAccountSpam').mockResolvedValue(
      freeResult as never,
    )
    const wrapper = await mountModal()
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('spamCheck.start'))!
    await startBtn.trigger('click')
    await flushPromises()
    expect(accountsApi.checkAccountSpam).toHaveBeenCalledWith(
      'mock-token',
      'acc-a',
    )
    expect(wrapper.text()).toContain('spamCheck.verdictFree')
    expect(wrapper.text()).toContain('free as a bird')
    expect(wrapper.emitted('success')).toBeTruthy()
  })

  it('limited 结果显示单向受限', async () => {
    vi.spyOn(accountsApi, 'checkAccountSpam').mockResolvedValue(
      limitedResult as never,
    )
    const wrapper = await mountModal()
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('spamCheck.start'))!
    await startBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('spamCheck.verdictLimited')
    expect(wrapper.text()).toContain('Unfortunately')
  })

  it('接口失败显示错误并可重试', async () => {
    vi.spyOn(accountsApi, 'checkAccountSpam').mockRejectedValue(
      new Error('boom'),
    )
    const wrapper = await mountModal()
    const startBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('spamCheck.start'))!
    await startBtn.trigger('click')
    await flushPromises()
    // 错误原文直接展示（getLocalizedErrorMessage 原样返回错误文案）
    expect(wrapper.text()).toContain('boom')
    const retryBtn = wrapper
      .findAll('button')
      .find((b) => b.text().includes('spamCheck.retry'))
    expect(retryBtn).toBeTruthy()
  })

  it('有上次结果时显示上次检测结论', async () => {
    const wrapper = await mountModal({
      status: 'limited',
      checked_at: '2026-11-05T06:00:00Z',
      summary: '部分限制',
    })
    expect(wrapper.text()).toContain('spamCheck.previous')
    expect(wrapper.text()).toContain('spamCheck.verdictLimited')
  })
})
