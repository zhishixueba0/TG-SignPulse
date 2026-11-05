<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { Check, Copy, Download, RefreshCw } from 'lucide-vue-next'
import Modal from '../Modal.vue'
import { exportStandaloneSession } from '../../lib/api'
import { getAuthToken } from '../../lib/api/core'
import { useI18n } from '../../composables/useI18n'
import { copyToClipboard } from '../../lib/clipboard'
import { getLocalizedErrorMessage } from '../../lib/types'

const { t } = useI18n()

const props = defineProps<{
  isOpen: boolean
  accountName: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

type Phase = 'idle' | 'running' | 'done' | 'error'

const phase = ref<Phase>('idle')
const elapsed = ref(0)
const error = ref('')
const sessionString = ref('')
const dcId = ref<number | null>(null)
const userId = ref<number | null>(null)
const copied = ref(false)

let timer: ReturnType<typeof setInterval> | null = null
let copyResetTimer: ReturnType<typeof setTimeout> | null = null
let runId = 0

const steps = computed(() => [
  t('accounts.deriveSessionStepToken'),
  t('accounts.deriveSessionStepAccept'),
  t('accounts.deriveSessionStepWait'),
  t('accounts.deriveSessionStepDone'),
])

/** 服务端同步执行（最长 60 秒）：前两步约在最初几秒完成，之后一直在等
 * Telegram 回执；按耗时估算当前步骤，仅作进度示意，不代表服务端实况。 */
const activeStep = computed(() => {
  if (phase.value === 'done') return 4
  if (elapsed.value < 3) return 0
  if (elapsed.value < 6) return 1
  return 2
})

const stopTimer = () => {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

const startExport = async () => {
  const token = getAuthToken()
  if (!token || !props.accountName) return
  const myRun = ++runId
  phase.value = 'running'
  elapsed.value = 0
  error.value = ''
  sessionString.value = ''
  dcId.value = null
  userId.value = null
  stopTimer()
  timer = setInterval(() => {
    elapsed.value += 1
  }, 1000)
  try {
    const res = await exportStandaloneSession(token, props.accountName)
    if (myRun !== runId) return
    sessionString.value = res.session_string || ''
    dcId.value = res.dc_id ?? null
    userId.value = res.user_id ?? null
    phase.value = 'done'
  } catch (e: unknown) {
    if (myRun !== runId) return
    // 后端错误码原文透传（如 EXPORT_LOGIN_TIMEOUT / 2FA_NOT_SUPPORTED）
    error.value = getLocalizedErrorMessage(e, t, t('accounts.deriveSessionFailed'))
    phase.value = 'error'
  } finally {
    if (myRun === runId) stopTimer()
  }
}

const copySessionString = async () => {
  if (!sessionString.value) return
  if (copyResetTimer !== null) clearTimeout(copyResetTimer)
  copied.value = await copyToClipboard(sessionString.value)
  if (copied.value) {
    copyResetTimer = setTimeout(() => {
      copied.value = false
      copyResetTimer = null
    }, 2000)
  }
}

const downloadSessionFile = () => {
  if (!sessionString.value) return
  const blob = new Blob([sessionString.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${props.accountName}.session.txt`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

watch(
  () => props.isOpen,
  (val) => {
    if (val && props.accountName) {
      startExport()
    } else if (!val) {
      runId += 1
      stopTimer()
      phase.value = 'idle'
      elapsed.value = 0
      error.value = ''
      sessionString.value = ''
      copied.value = false
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  runId += 1
  stopTimer()
  if (copyResetTimer !== null) clearTimeout(copyResetTimer)
})
</script>

<template>
  <Modal :isOpen="isOpen" :title="t('accounts.deriveSessionTitle')" maxWidthClass="max-w-xl" @close="emit('close')">
    <div class="space-y-4 text-xs">
      <p class="text-[var(--sp-text-muted)] leading-relaxed">
        {{ t('accounts.deriveSessionHint') }}
      </p>

      <ol class="space-y-2">
        <li
          v-for="(step, index) in steps"
          :key="step"
          class="flex items-center gap-2"
        >
          <span
            class="w-5 h-5 shrink-0 rounded-full inline-flex items-center justify-center text-[11px] font-medium"
            :class="
              phase === 'done' || index < activeStep
                ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/60 dark:text-emerald-300'
                : index === activeStep && phase === 'running'
                  ? 'bg-primary-100 text-primary-700 dark:bg-primary-900/60 dark:text-primary-300'
                  : 'bg-[var(--sp-surface-muted)] text-[var(--sp-text-muted)]'
            "
          >
            <RefreshCw v-if="index === activeStep && phase === 'running'" class="w-3 h-3 animate-spin" />
            <Check v-else-if="phase === 'done' || index < activeStep" class="w-3 h-3" />
            <span v-else>{{ index + 1 }}</span>
          </span>
          <span :class="index === activeStep && phase === 'running' ? 'font-medium text-[var(--sp-text)]' : 'text-[var(--sp-text-secondary)]'">
            {{ step }}
          </span>
        </li>
      </ol>

      <div v-if="phase === 'running'" class="text-[var(--sp-text-muted)]">
        {{ t('accounts.deriveSessionRunning') }} {{ t('accounts.deriveSessionElapsed', { seconds: elapsed }) }}
      </div>

      <div v-if="phase === 'error'" class="ui-alert-error" role="alert">
        {{ error }}
      </div>

      <div v-if="phase === 'done'" class="space-y-3">
        <p class="text-emerald-600 dark:text-emerald-400 font-medium">
          {{ t('accounts.deriveSessionSuccess') }}
        </p>
        <div class="grid grid-cols-2 gap-2 text-[11px] bg-[var(--sp-surface-subtle)] dark:bg-[var(--sp-surface-muted)] p-2.5 rounded border border-[var(--sp-border)] dark:border-[var(--sp-border)]">
          <div><span class="text-[var(--sp-text-muted)]">DC ID:</span> <span class="font-mono font-medium">{{ dcId ?? '-' }}</span></div>
          <div><span class="text-[var(--sp-text-muted)]">User ID:</span> <span class="font-mono font-medium">{{ userId ?? '-' }}</span></div>
        </div>
        <div>
          <label class="block text-[11px] font-medium text-[var(--sp-text-muted)] mb-1">
            Pyrogram / Kurigram SessionString
          </label>
          <textarea
            readonly
            rows="4"
            class="w-full font-mono text-[11px] p-2 rounded border border-[var(--sp-border-strong)] dark:border-[var(--sp-border)] bg-[var(--sp-surface-muted)] dark:bg-[var(--sp-surface-elevated)] select-all break-all"
            :value="sessionString"
          />
        </div>
        <p class="text-[var(--sp-text-muted)] leading-relaxed">
          {{ t('accounts.exportStandaloneSessionNotice') }}
        </p>
      </div>
    </div>

    <template #footer>
      <div class="flex items-center justify-between w-full">
        <button
          v-if="phase === 'done'"
          type="button"
          class="ui-btn-secondary !px-3 !py-1.5 !text-xs inline-flex items-center gap-1.5"
          @click="downloadSessionFile"
        >
          <Download class="w-3.5 h-3.5" />
          {{ t('accounts.downloadSessionFile') }}
        </button>
        <span v-else />
        <div class="flex items-center gap-2">
          <button
            v-if="phase === 'done'"
            type="button"
            class="ui-btn-primary !px-3 !py-1.5 !text-xs inline-flex items-center gap-1.5"
            @click="copySessionString"
          >
            <Check v-if="copied" class="w-3.5 h-3.5 text-emerald-400" />
            <Copy v-else class="w-3.5 h-3.5" />
            {{ copied ? t('common.copied') : t('common.copy') }}
          </button>
          <button
            v-if="phase === 'error'"
            type="button"
            class="ui-btn-primary !px-3 !py-1.5 !text-xs"
            @click="startExport"
          >
            {{ t('common.retry') }}
          </button>
          <button
            type="button"
            class="ui-btn-secondary !px-3 !py-1.5 !text-xs"
            @click="emit('close')"
          >
            {{ t('common.close') }}
          </button>
        </div>
      </div>
    </template>
  </Modal>
</template>
