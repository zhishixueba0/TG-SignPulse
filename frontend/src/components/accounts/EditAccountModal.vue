<script setup lang="ts">
import { ref, watch } from 'vue'
import Modal from '../Modal.vue'
import AccountImpactModal from './AccountImpactModal.vue'
import { previewAccountChange, applyAccountChange, type AccountChangePlan } from '../../lib/api/accounts'
import { updateAccount } from '../../lib/api'
import { listProxies, type ProxyItem, type ProxyMode } from '../../lib/api/proxies'
import { getAuthToken } from '../../lib/api/core'
import { useI18n } from '../../composables/useI18n'
import { useToast } from '../../composables/useToast'
import type { AccountUiItem } from '../../lib/types'
import { getLocalizedErrorMessage } from '../../lib/types'

const { t } = useI18n()
const toast = useToast()

const props = defineProps<{
  isOpen: boolean
  account: AccountUiItem
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'success'): void
  (e: 'relogin', name: string): void
  /** 离场动画播完：显式转发，供父组件串行打开下一个弹窗 */
  (e: 'closed'): void
}>()

type ProxyModeOption = 'inherit' | 'direct' | 'pool' | 'custom'

const form = ref({
  new_account_name: '',
  remark: '',
  proxyMode: 'inherit' as ProxyModeOption,
  proxyId: '',
  proxyUrl: '',
})
const loading = ref(false)
const error = ref('')
const poolProxies = ref<ProxyItem[]>([])
const poolFailed = ref(false)

const loadPool = async () => {
  const token = getAuthToken()
  if (!token) return
  try {
    poolFailed.value = false
    const res = await listProxies(token)
    poolProxies.value = res.proxies
  } catch {
    // 代理池加载失败：退回自由文本输入，不阻塞编辑
    poolFailed.value = true
    poolProxies.value = []
  }
}

const handleClosed = () => emit('closed')

watch(() => props.isOpen, (val) => {
  if (val && props.account) {
    const raw = props.account.raw
    const mode = raw.proxy_mode
    form.value = {
      new_account_name: props.account.name || '',
      remark: props.account.remark || '',
      proxyMode: mode === 'inherit' || mode === 'direct' || mode === 'pool' || mode === 'custom'
        ? mode
        : raw.proxy
          ? 'custom'
          : 'inherit',
      proxyId: raw.proxy_id || '',
      proxyUrl: raw.proxy || '',
    }
    error.value = ''
    void loadPool()
  }
})

const renamePlan = ref<AccountChangePlan | null>(null)
const handleSave = async (confirmed = false) => {
  const token = getAuthToken()
  if (!token || !props.account) return

  loading.value = true
  error.value = ''
  try {
    let accountName = props.account.name
    if (form.value.new_account_name && form.value.new_account_name !== accountName) {
      if (!confirmed) {
        renamePlan.value = await previewAccountChange(token, accountName, 'rename', form.value.new_account_name)
        return
      }
      if (!renamePlan.value) return
      accountName = (await applyAccountChange(token, renamePlan.value)).account_name
      renamePlan.value = null
      emit('success')
    }
    // 代理线路：pool → proxy_id，custom → proxy（自由文本），inherit/direct → 两者都清空
    const proxyMode: ProxyMode = poolFailed.value ? 'custom' : form.value.proxyMode
    await updateAccount(token, accountName, {
      new_account_name: null,
      remark: form.value.remark || null,
      proxy_mode: proxyMode,
      proxy_id: proxyMode === 'pool' ? (form.value.proxyId || null) : null,
      proxy: proxyMode === 'custom' ? (form.value.proxyUrl.trim() || null) : null,
    })
    toast.success(t('editAccount.saveSuccess'))
    emit('success')
    emit('close')
  } catch (e: unknown) {
    // 账号在线时后端返回 409：原样展示后端文案
    error.value = getLocalizedErrorMessage(e, t, t('editAccount.saveFailed'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AccountImpactModal :isOpen="!!renamePlan" :plan="renamePlan" :loading="loading" :error="error" @close="renamePlan = null" @closed="handleClosed" @apply="handleSave(true)" />
  <Modal :isOpen="isOpen" @close="$emit('close')" @closed="handleClosed" :title="t('editAccount.title')">
    <div class="space-y-4">
      <div v-if="error" class="ui-alert-error" role="alert">
        {{ error }}
      </div>

      <div class="space-y-1.5">
        <label class="ui-label" for="edit-account-name">{{ t('editAccount.nameLabel') }}</label>
        <input 
          id="edit-account-name"
          v-model="form.new_account_name"
          type="text" 
          :placeholder="t('editAccount.namePlaceholder')"
          class="ui-input"
        >
      </div>

      <div class="space-y-1.5">
        <label class="ui-label" for="edit-account-remark">{{ t('editAccount.remarkLabel') }}</label>
        <input 
          id="edit-account-remark"
          v-model="form.remark"
          type="text" 
          :placeholder="t('editAccount.remarkPlaceholder')"
          class="ui-input"
        >
      </div>

      <div class="space-y-1.5">
        <span class="ui-label">{{ t('editAccount.proxyModeLabel') }}</span>
        <div v-if="poolFailed" class="space-y-1.5">
          <input
            v-model="form.proxyUrl"
            type="text"
            placeholder="socks5://..."
            class="ui-input"
          >
          <p class="text-xs text-[var(--sp-text-muted)]">{{ t('editAccount.proxyLoadFailed') }}</p>
        </div>
        <div v-else class="space-y-2">
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="form.proxyMode" type="radio" value="inherit" class="!w-auto">
            {{ t('editAccount.proxyModeInherit') }}
          </label>
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="form.proxyMode" type="radio" value="direct" class="!w-auto">
            {{ t('editAccount.proxyModeDirect') }}
          </label>
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="form.proxyMode" type="radio" value="pool" class="!w-auto" :disabled="poolProxies.length === 0">
            {{ t('editAccount.proxyModePool') }}
          </label>
          <select
            v-if="form.proxyMode === 'pool'"
            v-model="form.proxyId"
            class="ui-input"
          >
            <option value="" disabled>{{ t('editAccount.proxySelectPlaceholder') }}</option>
            <option v-for="p in poolProxies" :key="p.id" :value="p.id">{{ p.label }} — {{ p.display_url }}</option>
          </select>
          <label class="flex items-center gap-2 text-sm text-[var(--sp-text)] cursor-pointer">
            <input v-model="form.proxyMode" type="radio" value="custom" class="!w-auto">
            {{ t('editAccount.proxyModeCustom') }}
          </label>
          <input
            v-if="form.proxyMode === 'custom'"
            v-model="form.proxyUrl"
            type="text"
            :placeholder="t('editAccount.proxyCustomPlaceholder')"
            class="ui-input"
          >
        </div>
      </div>

      <div v-if="account.raw.added_at" class="flex gap-1.5 text-sm pt-1 border-t border-[var(--sp-border)]">
        <span class="text-[var(--sp-text-muted)] shrink-0">{{ t('editAccount.addedAt') }}：</span>
        <span class="text-[var(--sp-text-secondary)]">{{ account.raw.added_at.slice(0, 10) }}</span>
      </div>
    </div>

    <template #footer>
      <div class="flex-1">
        <button 
          type="button"
          class="ui-btn-danger !px-4 !py-2"
          @click="$emit('relogin', account?.name)"
        >
          {{ t('editAccount.relogin') }}
        </button>
      </div>
      <button 
        type="button"
        class="ui-btn-secondary !border-transparent !bg-transparent !px-4 !py-2"
        @click="$emit('close')"
      >
        {{ t('common.cancel') }}
      </button>
      <button 
        type="button"
        class="ui-btn-primary !px-4 !py-2"
        :disabled="loading"
        @click="handleSave()"
      >
        {{ loading ? t('editAccount.saving') : t('editAccount.saveChanges') }}
      </button>
    </template>
  </Modal>
</template>
