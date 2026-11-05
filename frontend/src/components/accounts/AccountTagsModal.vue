<script setup lang="ts">
import { ref, watch } from 'vue'
import Modal from '../Modal.vue'
import { updateAccount } from '../../lib/api'
import { getAuthToken } from '../../lib/api/core'
import { useI18n } from '../../composables/useI18n'
import { useToast } from '../../composables/useToast'
import type { AccountUiItem } from '../../lib/types'
import { getLocalizedErrorMessage } from '../../lib/types'
import { formatTagsInput, mergeTags, parseTagsInput } from '../../lib/tag-text'

const { t } = useI18n()
const toast = useToast()

const props = defineProps<{
  isOpen: boolean
  account: AccountUiItem | null
  /** append：卡片“+ 添加标签”，只追加不覆盖；edit：“···”菜单“编辑标签”，全量编辑 */
  mode: 'append' | 'edit'
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'success'): void
}>()

const input = ref('')
const loading = ref(false)
const error = ref('')

watch(() => props.isOpen, (val) => {
  if (!val) return
  // 全量编辑回填已有标签；追加模式从空开始
  input.value = props.mode === 'edit'
    ? formatTagsInput(props.account?.raw.tags ?? [])
    : ''
  error.value = ''
})

const title = () => props.mode === 'edit' ? t('accounts.editTags') : t('accounts.addTags')
const hint = () => props.mode === 'edit' ? t('accounts.editTagsHint') : t('accounts.addTagsHint')

const handleSave = async () => {
  const token = getAuthToken()
  if (!token || !props.account) return
  const typed = parseTagsInput(input.value)
  const nextTags = props.mode === 'edit'
    ? typed
    : mergeTags(props.account.raw.tags ?? [], typed)
  loading.value = true
  error.value = ''
  try {
    await updateAccount(token, props.account.name, { tags: nextTags })
    toast.success(t('editAccount.saveSuccess'))
    emit('success')
    emit('close')
  } catch (e: unknown) {
    error.value = getLocalizedErrorMessage(e, t, t('editAccount.saveFailed'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <Modal :isOpen="isOpen" :title="title()" @close="$emit('close')">
    <div class="space-y-4">
      <div v-if="error" class="ui-alert-error" role="alert">
        {{ error }}
      </div>
      <div v-if="mode === 'edit' && (account?.raw.tags ?? []).length > 0" class="flex flex-wrap gap-1">
        <span v-for="tag in account?.raw.tags ?? []" :key="tag" class="ui-badge !py-0 !px-1.5">{{ tag }}</span>
      </div>
      <div class="space-y-1.5">
        <label class="ui-label" for="account-tags-input">{{ title() }}</label>
        <input
          id="account-tags-input"
          v-model="input"
          type="text"
          class="ui-input"
          :placeholder="t('accounts.tagsPlaceholder')"
          @keydown.enter.prevent="handleSave"
        >
        <p class="text-xs text-[var(--sp-text-muted)]">{{ hint() }}</p>
      </div>
    </div>
    <template #footer>
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
        @click="handleSave"
      >
        {{ loading ? t('editAccount.saving') : t('common.confirm') }}
      </button>
    </template>
  </Modal>
</template>
