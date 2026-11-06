<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { LoaderCircle, RefreshCw } from 'lucide-vue-next'
import Modal from '../Modal.vue'
import { checkAccountSpam, type SpamCheckResult } from '../../lib/api/accounts'
import { getAuthToken } from '../../lib/api/core'
import { useI18n } from '../../composables/useI18n'
import { getLocalizedErrorMessage } from '../../lib/types'

const { t } = useI18n()

const props = defineProps<{
  isOpen: boolean
  accountName: string
  /** 上次检测结果（卡片已有时透传，弹窗里展示“上次检测”） */
  previous?: {
    status?: string | null
    checked_at?: string | null
    summary?: string | null
  } | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'success'): void
}>()

type Phase = 'idle' | 'running' | 'done' | 'error'

const phase = ref<Phase>('idle')
const error = ref('')
const result = ref<SpamCheckResult | null>(null)
const elapsed = ref(0)

let timer: ReturnType<typeof setInterval> | null = null

const stopTimer = () => {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

onUnmounted(stopTimer)

watch(
  () => props.isOpen,
  (val) => {
    if (val) {
      phase.value = 'idle'
      error.value = ''
      result.value = null
      elapsed.value = 0
      stopTimer()
    } else {
      stopTimer()
    }
  },
)

/** 结论标题：按后端 status 分档 */
const verdictTitle = computed(() => {
  const s = result.value?.status
  if (s === 'free') return t('spamCheck.verdictFree')
  if (s === 'limited') return t('spamCheck.verdictLimited')
  if (s === 'banned') return t('spamCheck.verdictBanned')
  return t('spamCheck.verdictUnknown')
})

/** 结论颜色：正常绿、受限/冻结红、未知灰 */
const verdictClass = computed(() => {
  const s = result.value?.status
  if (s === 'free') return 'text-emerald-600 dark:text-emerald-400'
  if (s === 'limited' || s === 'banned')
    return 'text-rose-600 dark:text-rose-400'
  return 'text-[var(--sp-text-muted)]'
})

const previousLabel = computed(() => {
  const p = props.previous
  if (!p?.status) return ''
  const map: Record<string, string> = {
    free: t('spamCheck.verdictFree'),
    limited: t('spamCheck.verdictLimited'),
    banned: t('spamCheck.verdictBanned'),
    unknown: t('spamCheck.verdictUnknown'),
  }
  return map[p.status] ?? p.status
})

const startCheck = async () => {
  const token = getAuthToken()
  if (!token) return
  phase.value = 'running'
  error.value = ''
  result.value = null
  elapsed.value = 0
  stopTimer()
  timer = setInterval(() => {
    elapsed.value += 1
  }, 1000)
  try {
    const res = await checkAccountSpam(token, props.accountName)
    result.value = res
    phase.value = 'done'
    emit('success')
  } catch (e: unknown) {
    error.value = getLocalizedErrorMessage(e, t, t('spamCheck.checkFailed'))
    phase.value = 'error'
  } finally {
    stopTimer()
  }
}
</script>

<template>
  <Modal :isOpen="isOpen" @close="$emit('close')" :title="t('spamCheck.title', { name: accountName })">
    <div class="space-y-4">
      <!-- 说明 -->
      <p v-if="phase === 'idle'" class="text-sm text-[var(--sp-text-secondary)]">
        {{ t('spamCheck.intro') }}
      </p>
      <p v-if="phase === 'idle' && previous?.status" class="text-xs text-[var(--sp-text-muted)]">
        {{ t('spamCheck.previous') }}：{{ previousLabel }}
        <span v-if="previous.checked_at">（{{ previous.checked_at }}）</span>
      </p>

      <!-- 进行中 -->
      <div v-if="phase === 'running'" class="space-y-3">
        <div class="flex items-center gap-2 text-sm text-[var(--sp-text)]">
          <LoaderCircle class="w-4 h-4 animate-spin shrink-0" />
          {{ t('spamCheck.running') }}
        </div>
        <div class="h-1.5 rounded-full bg-[var(--sp-border)] overflow-hidden">
          <div
            class="h-full rounded-full bg-[var(--sp-accent)] transition-all duration-1000"
            :style="{ width: `${Math.min(100, Math.round((elapsed / 16) * 100))}%` }"
          />
        </div>
        <p class="text-xs text-[var(--sp-text-muted)]">
          {{ t('spamCheck.runningHint', { seconds: elapsed }) }}
        </p>
      </div>

      <!-- 出错 -->
      <div v-if="phase === 'error'" class="ui-alert-error" role="alert">
        {{ error }}
      </div>

      <!-- 结果 -->
      <div v-if="phase === 'done' && result" class="space-y-3">
        <p class="text-base font-medium" :class="verdictClass">{{ verdictTitle }}</p>
        <p class="text-sm text-[var(--sp-text-secondary)]">{{ result.summary }}</p>
        <p v-if="result.until" class="text-sm text-[var(--sp-text-secondary)]">
          {{ t('spamCheck.until') }}：{{ result.until }}
        </p>
        <div v-if="result.message">
          <span class="ui-label">{{ t('spamCheck.replyTitle') }}</span>
          <pre class="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--sp-surface-2)] p-3 text-xs text-[var(--sp-text-secondary)]">{{ result.message }}</pre>
        </div>
        <p class="text-xs text-[var(--sp-text-muted)]">
          {{ t('spamCheck.checkedAt') }}：{{ result.checked_at }}
        </p>
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <button
          v-if="phase === 'idle'"
          type="button"
          class="ui-btn-secondary"
          @click="$emit('close')"
        >
          {{ t('common.cancel') }}
        </button>
        <button
          v-if="phase === 'idle'"
          type="button"
          class="ui-btn-primary"
          @click="startCheck"
        >
          {{ t('spamCheck.start') }}
        </button>
        <button
          v-if="phase === 'error' || phase === 'done'"
          type="button"
          class="ui-btn-secondary inline-flex items-center gap-1"
          @click="startCheck"
        >
          <RefreshCw class="w-3.5 h-3.5" />
          {{ t('spamCheck.retry') }}
        </button>
        <button
          v-if="phase === 'error' || phase === 'done'"
          type="button"
          class="ui-btn-primary"
          @click="$emit('close')"
        >
          {{ t('common.close') }}
        </button>
      </div>
    </template>
  </Modal>
</template>
