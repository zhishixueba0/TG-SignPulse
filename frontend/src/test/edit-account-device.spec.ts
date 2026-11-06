import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import EditAccountModal from '../components/accounts/EditAccountModal.vue'
import * as accountsApi from '../lib/api/accounts'
import * as api from '../lib/api'
import * as coreApi from '../lib/api/core'
import * as proxiesApi from '../lib/api/proxies'

vi.mock('../composables/useI18n', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))

vi.mock('../composables/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}))

// Modal 用最小替身，只渲染默认插槽
const ModalStub = defineComponent({
  name: 'Modal',
  emits: ['close', 'closed'],
  template: `<div><slot /><slot name="footer" /></div>`,
})

const account = {
  name: 'acc-a',
  remark: '',
  raw: {
    proxy: '',
    device_family: null,
    device_profile: null,
  },
} as never

const presets = {
  desktop: [
    { device_model: 'PC 64bit', system_version: 'Windows 11', app_version: '5.5.0 x64', lang_code: 'zh-hans', system_lang_code: 'zh-CN' },
  ],
  linux: [
    { device_model: 'PC 64bit', system_version: 'Ubuntu 24.04', app_version: '5.4.1 x64', lang_code: 'en', system_lang_code: 'en-US' },
    { device_model: 'PC 64bit', system_version: 'Debian 12', app_version: '5.5.0 x64', lang_code: 'zh-hans', system_lang_code: 'zh-CN' },
  ],
}

async function mountModal() {
  const wrapper = mount(EditAccountModal, {
    props: { isOpen: false, account },
    global: { stubs: { Modal: ModalStub, AccountImpactModal: true } },
  })
  // 模拟真实打开：isOpen 由 false 变 true，触发 watch 加载预设
  await wrapper.setProps({ isOpen: true })
  await flushPromises()
  return wrapper
}

describe('EditAccountModal 设备指纹', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(coreApi, 'getAuthToken').mockReturnValue('mock-token')
    vi.spyOn(proxiesApi, 'listProxies').mockResolvedValue({ proxies: [] } as never)
    vi.spyOn(accountsApi, 'listDeviceProfiles').mockResolvedValue({
      success: true,
      families: presets,
    })
    vi.spyOn(api, 'updateAccount').mockResolvedValue({ success: true } as never)
  })

  it('设备族下拉包含 Linux，选 Linux 后列出 Linux 预设', async () => {
    const wrapper = await mountModal()
    await flushPromises()
    const options = wrapper.findAll('select option').map((o) => o.attributes('value'))
    expect(options).toContain('linux')
    // 选 Linux
    const select = wrapper.findAll('select')[0]
    await select.setValue('linux')
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Ubuntu 24.04')
    expect(text).toContain('Debian 12')
  })

  it('选 Linux + 指定预设后保存，提交 device_family 与完整画像', async () => {
    const wrapper = await mountModal()
    await flushPromises()
    const select = wrapper.findAll('select')[0]
    await select.setValue('linux')
    await flushPromises()
    // 选第二个预设（Debian 12）
    const radios = wrapper.findAll('input[type="radio"][value="1"]')
    expect(radios.length).toBeGreaterThan(0)
    await radios[0].setValue()
    // 点保存
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('editAccount.saveChanges'))
    expect(saveBtn).toBeTruthy()
    await saveBtn!.trigger('click')
    await flushPromises()
    const updateAccount = api.updateAccount as unknown as ReturnType<typeof vi.fn>
    expect(updateAccount).toHaveBeenCalled()
    const payload = (updateAccount as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][2] as {
      device_family: string
      device_profile: unknown
    }
    expect(payload.device_family).toBe('linux')
    expect(payload.device_profile).toEqual(presets.linux[1])
  })

  it('选 Linux + 随机时只传族，不传具体画像', async () => {
    const wrapper = await mountModal()
    await flushPromises()
    const select = wrapper.findAll('select')[0]
    await select.setValue('linux')
    await flushPromises()
    // 默认就是 random（不点具体预设）
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('editAccount.saveChanges'))
    await saveBtn!.trigger('click')
    await flushPromises()
    const updateAccount = api.updateAccount as unknown as { mock: { calls: unknown[][] } }
    const payload = updateAccount.mock.calls[0][2] as { device_family: string; device_profile: unknown }
    expect(payload.device_family).toBe('linux')
    expect(payload.device_profile).toBeNull()
  })

  it('focusSection=device 时打开，滚动定位到指纹区', async () => {
    const scrollSpy = vi.fn()
    // jsdom 未实现 scrollIntoView：先在原型上挂一个 mock
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      value: scrollSpy,
      configurable: true,
      writable: true,
    })
    const wrapper = mount(EditAccountModal, {
      props: { isOpen: false, account, focusSection: 'device' },
      global: { stubs: { Modal: ModalStub, AccountImpactModal: true } },
    })
    await wrapper.setProps({ isOpen: true })
    await flushPromises()
    expect(scrollSpy).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('普通打开（无 focusSection）时不滚动', async () => {
    const scrollSpy = vi.fn()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      value: scrollSpy,
      configurable: true,
      writable: true,
    })
    const wrapper = await mountModal()
    await flushPromises()
    expect(scrollSpy).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
