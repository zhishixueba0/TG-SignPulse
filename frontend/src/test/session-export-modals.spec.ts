import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SessionPackageModal from '../components/accounts/SessionPackageModal.vue'
import SessionExportModal from '../components/accounts/SessionExportModal.vue'
import * as api from '../lib/api'
import * as coreApi from '../lib/api/core'

vi.mock('../composables/useI18n', () => ({
  useI18n: () => ({
    t: (k: string) => k,
  }),
}))

const mockToastSuccess = vi.fn()

vi.mock('../composables/useToast', () => ({
  useToast: () => ({ success: mockToastSuccess, error: vi.fn() }),
}))

describe('SessionPackageModal', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
    vi.spyOn(coreApi, 'getAuthToken').mockReturnValue('mock-token')
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('密码为空时导出按钮禁用，不调用导出', async () => {
    const exportSpy = vi.spyOn(api, 'exportSessionPackage').mockResolvedValue({ filename: 'a.zip' })

    const wrapper = mount(SessionPackageModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const downloadBtn = buttons.find((b) =>
      (b.textContent || '').includes('accounts.sessionPackageDownload'),
    ) as HTMLButtonElement
    expect(downloadBtn).toBeDefined()
    expect(downloadBtn.disabled).toBe(true)

    downloadBtn.click()
    await flushPromises()

    expect(exportSpy).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('密码确认后调导出并关闭弹窗', async () => {
    const exportSpy = vi.spyOn(api, 'exportSessionPackage').mockResolvedValue({ filename: 'a.zip' })

    const wrapper = mount(SessionPackageModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    const input = document.body.querySelector(
      '#session-package-password',
    ) as HTMLInputElement
    input.value = 'panel-pw'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const downloadBtn = buttons.find((b) =>
      (b.textContent || '').includes('accounts.sessionPackageDownload'),
    )
    downloadBtn!.click()
    await flushPromises()

    expect(exportSpy).toHaveBeenCalledWith('mock-token', 'test-account', 'panel-pw', 'session')
    expect(mockToastSuccess).toHaveBeenCalledWith('accounts.sessionPackageDownloaded')
    expect(wrapper.emitted('close')).toBeTruthy()

    wrapper.unmount()
  })

  it('选 TData 格式时调导出并传 tdata', async () => {
    const exportSpy = vi.spyOn(api, 'exportSessionPackage').mockResolvedValue({ filename: 'a-tdata.zip' })

    const wrapper = mount(SessionPackageModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    const tdataRadio = document.body.querySelector(
      'input[type="radio"][value="tdata"]',
    ) as HTMLInputElement
    expect(tdataRadio).toBeTruthy()
    tdataRadio.click()
    await flushPromises()

    const input = document.body.querySelector(
      '#session-package-password',
    ) as HTMLInputElement
    input.value = 'panel-pw'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const downloadBtn = buttons.find((b) =>
      (b.textContent || '').includes('accounts.sessionPackageDownload'),
    )
    downloadBtn!.click()
    await flushPromises()

    expect(exportSpy).toHaveBeenCalledWith('mock-token', 'test-account', 'panel-pw', 'tdata')
    expect(wrapper.emitted('close')).toBeTruthy()

    wrapper.unmount()
  })

  it('面板密码错误时显示明确文案，不关闭', async () => {
    vi.spyOn(api, 'exportSessionPackage').mockRejectedValue(
      new Error('PANEL_PASSWORD_WRONG'),
    )

    const wrapper = mount(SessionPackageModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    const input = document.body.querySelector(
      '#session-package-password',
    ) as HTMLInputElement
    input.value = 'wrong-pw'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const downloadBtn = buttons.find((b) =>
      (b.textContent || '').includes('accounts.sessionPackageDownload'),
    )
    downloadBtn!.click()
    await flushPromises()

    expect(document.body.textContent).toContain('accounts.sessionPackagePasswordWrong')
    expect(wrapper.emitted('close')).toBeFalsy()

    wrapper.unmount()
  })

  it('限流时显示过于频繁文案', async () => {
    vi.spyOn(api, 'exportSessionPackage').mockRejectedValue(
      new Error('导出会话包过于频繁，请稍后再试'),
    )

    const wrapper = mount(SessionPackageModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    const input = document.body.querySelector(
      '#session-package-password',
    ) as HTMLInputElement
    input.value = 'panel-pw'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const downloadBtn = buttons.find((b) =>
      (b.textContent || '').includes('accounts.sessionPackageDownload'),
    )
    downloadBtn!.click()
    await flushPromises()

    expect(document.body.textContent).toContain('accounts.sessionPackageTooFrequent')

    wrapper.unmount()
  })
})

describe('SessionExportModal', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
    vi.spyOn(coreApi, 'getAuthToken').mockReturnValue('mock-token')
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('打开后自动开始派生导出，成功展示会话字符串与 DC/用户 ID', async () => {
    const exportSpy = vi.spyOn(api, 'exportStandaloneSession').mockResolvedValue({
      success: true,
      session_string: 'derived-session-string',
      dc_id: 1,
      user_id: 8789956089,
      message: 'ok',
    })

    const wrapper = mount(SessionExportModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    expect(exportSpy).toHaveBeenCalledWith('mock-token', 'test-account')
    const textarea = document.body.querySelector('textarea') as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    expect(textarea.value).toBe('derived-session-string')
    expect(document.body.textContent).toContain('8789956089')
    expect(document.body.textContent).toContain('accounts.deriveSessionSuccess')

    wrapper.unmount()
  })

  it('失败时透传后端错误码原文，并可重试', async () => {
    const exportSpy = vi.spyOn(api, 'exportStandaloneSession')
    exportSpy.mockRejectedValue(
      new Error('EXPORT_LOGIN_TIMEOUT: Timeout waiting for candidate authorization'),
    )

    const wrapper = mount(SessionExportModal, {
      props: { isOpen: true, accountName: 'test-account' },
      attachTo: document.body,
    })
    await flushPromises()

    expect(document.body.textContent).toContain('EXPORT_LOGIN_TIMEOUT')

    exportSpy.mockResolvedValue({
      success: true,
      session_string: 'retry-ok-string',
      dc_id: 1,
      user_id: 123,
      message: 'ok',
    })
    const buttons = Array.from(document.body.querySelectorAll('button'))
    const retryBtn = buttons.find((b) =>
      (b.textContent || '').includes('common.retry'),
    )
    expect(retryBtn).toBeDefined()
    retryBtn!.click()
    await flushPromises()

    expect(exportSpy).toHaveBeenCalledTimes(2)
    const textarea = document.body.querySelector('textarea') as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    expect(textarea.value).toBe('retry-ok-string')

    wrapper.unmount()
  })
})
