<script setup lang="ts">
import { ref, watch } from 'vue'
import { Download } from 'lucide-vue-next'
import Modal from '../Modal.vue'
import { exportSessionPackage } from '../../lib/api'
import { getAuthToken } from '../../lib/api/core'
import { useI18n } from '../../composables/useI18n'
import { useToast } from '../../composables/useToast'
import { getLocalizedErrorMessage } from '../../lib/types'

const { t } = useI18n()
const toast = useToast()

const props = defineProps<{
  isOpen: boolean
  accountName: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const password = ref('')
const loading = ref(false)
const error = ref('')
const exportFormat = ref<'session' | 'tdata'>('session')

watch(
  () => props.isOpen,
  (val) => {
    if (!val) return
    password.value = ''
    error.value = ''
    exportFormat.value = 'session'
  },
)

const handleExport = async () => {
  const token = getAuthToken()
  if (!token || !props.accountName || loading.value) return
  if (!password.value) {
    error.value = t('accounts.sessionPackagePasswordPlaceholder')
    return
  }
  loading.value = true
  error.value = ''
  try {
    await exportSessionPackage(token, props.accountName, password.value, exportFormat.value)
    toast.success(t('accounts.sessionPackageDownloaded'))
    emit('close')
  } catch (e: unknown) {
    const message = getLocalizedErrorMessage(e, t, t('accounts.sessionPackageFailed'))
    // 后端错误码原文透传；面板密码错误与限流给明确文案
    if (message.includes('PANEL_PASSWORD_WRONG')) {
      error.value = t('accounts.sessionPackagePasswordWrong')
    } else if (message.includes('429') || message.includes('频繁')) {
      error.value = t('accounts.sessionPackageTooFrequent')
    } else {
      error.value = message
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <Modal :isOpen="isOpen" :title="t('accounts.sessionPackageTitle')" @close="emit('close')">
    <div class="space-y-4">
      <p class="text-xs text-[var(--sp-text-muted)] leading-relaxed">
        {{ t('accounts.sessionPackageHint') }}
      </p>
      <div class="space-y-1.5">
        <span class="ui-label">{{ t('accounts.sessionPackageFormatLabel') }}</span>
        <div class="flex flex-wrap gap-x-4 gap-y-1.5">
          <label class="flex items-center gap-1.5 text-xs text-[var(--sp-text-secondary)] cursor-pointer">
            <input v-model="exportFormat" type="radio" value="session" class="!w-auto">
            {{ t('accounts.sessionPackageFormatSession') }}
          </label>
          <label class="flex items-center gap-1.5 text-xs text-[var(--sp-text-secondary)] cursor-pointer">
            <input v-model="exportFormat" type="radio" value="tdata" class="!w-auto">
            {{ t('accounts.sessionPackageFormatTdata') }}
          </label>
        </div>
        <p class="text-xs text-[var(--sp-text-muted)] leading-relaxed">
          {{ exportFormat === 'tdata' ? t('accounts.sessionPackageFormatTdataHint') : t('accounts.sessionPackageFormatSessionHint') }}
        </p>
      </div>
      <div v-if="error" class="ui-alert-error" role="alert">
        {{ error }}
      </div>
      <div class="space-y-1.5">
        <label class="ui-label" for="session-package-password">
          {{ t('accounts.sessionPackagePasswordLabel') }}
        </label>
        <input
          id="session-package-password"
          v-model="password"
          type="password"
          class="ui-input"
          autocomplete="current-password"
          :placeholder="t('accounts.sessionPackagePasswordPlaceholder')"
          @keydown.enter.prevent="handleExport"
        >
      </div>
    </div>
    <template #footer>
      <button
        type="button"
        class="ui-btn-secondary !border-transparent !bg-transparent !px-4 !py-2"
        @click="emit('close')"
      >
        {{ t('common.cancel') }}
      </button>
      <button
        type="button"
        class="ui-btn-primary !px-4 !py-2 inline-flex items-center gap-1.5"
        :disabled="loading || !password"
        @click="handleExport"
      >
        <Download class="w-3.5 h-3.5" />
        {{ loading ? t('accounts.sessionPackageDownloading') : t('accounts.sessionPackageDownload') }}
      </button>
    </template>
  </Modal>
</template>
