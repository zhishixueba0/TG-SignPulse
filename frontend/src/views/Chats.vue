<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessageSquareText, RefreshCw, Send, X } from 'lucide-vue-next'
import { getAuthToken } from '../lib/api/core'
import {
  fetchChatMediaBlob,
  listChatDialogs,
  listChatMessages,
  pressChatButton,
  sendChatMessage,
  type ChatButton,
  type ChatDialog,
  type ChatMessage,
} from '../lib/api'
import { useAccountsStore } from '../stores/accounts'
import { useI18n } from '../composables/useI18n'
import { useToast } from '../composables/useToast'
import { notifyApiError } from '../lib/notify'
import { sortChatMessagesAsc, truncateDialogPreview } from '../lib/chat-map'
import { devLog } from '../lib/devLog'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const toast = useToast()
const accountsStore = useAccountsStore()

const accountNames = ref<string[]>([])
const selectedAccount = ref('')
const dialogs = ref<ChatDialog[]>([])
const dialogsLoading = ref(false)
const dialogsError = ref(false)
const selectedChatId = ref<number | string | null>(null)
const messages = ref<ChatMessage[]>([])
const messagesLoading = ref(false)
const messagesError = ref(false)
const sending = ref(false)
const draft = ref('')
const messagesBox = ref<HTMLElement | null>(null)
const mediaUrls = ref(new Map<number, string>())
const fullImageUrl = ref('')
const fullImageLoading = ref(false)
const pressingButton = ref(false)

const clearMediaUrls = () => {
  for (const url of mediaUrls.value.values()) URL.revokeObjectURL(url)
  mediaUrls.value.clear()
  if (fullImageUrl.value) {
    URL.revokeObjectURL(fullImageUrl.value)
    fullImageUrl.value = ''
  }
}

const mediaThumbUrl = (msg: ChatMessage): string | undefined =>
  msg.id == null ? undefined : mediaUrls.value.get(msg.id)

const loadMediaThumbs = async () => {
  const token = getAuthToken()
  if (!token || !selectedAccount.value || selectedChatId.value === null) return
  for (const msg of messages.value) {
    if (msg.media_type !== 'photo' || msg.id == null || mediaUrls.value.has(msg.id)) continue
    try {
      const blob = await fetchChatMediaBlob(token, selectedAccount.value, selectedChatId.value, msg.id, true)
      mediaUrls.value.set(msg.id, URL.createObjectURL(blob))
    } catch (e: unknown) {
      devLog.error('Failed to load chat image thumbnail', e)
    }
  }
}

const openFullImage = async (msg: ChatMessage) => {
  const token = getAuthToken()
  if (!token || !selectedAccount.value || selectedChatId.value === null || msg.id == null || fullImageLoading.value) return
  fullImageLoading.value = true
  try {
    const blob = await fetchChatMediaBlob(token, selectedAccount.value, selectedChatId.value, msg.id, false)
    if (fullImageUrl.value) URL.revokeObjectURL(fullImageUrl.value)
    fullImageUrl.value = URL.createObjectURL(blob)
  } catch (e: unknown) {
    notifyApiError(e, 'chats.imageLoadFailed')
  } finally {
    fullImageLoading.value = false
  }
}

const closeFullImage = () => {
  if (fullImageUrl.value) URL.revokeObjectURL(fullImageUrl.value)
  fullImageUrl.value = ''
}

const handlePressButton = async (msg: ChatMessage, button: ChatButton) => {
  const token = getAuthToken()
  if (!token || !selectedAccount.value || selectedChatId.value === null || msg.id == null || pressingButton.value) return
  pressingButton.value = true
  try {
    const res = await pressChatButton(token, selectedAccount.value, selectedChatId.value, msg.id, button.row, button.col)
    if (res.action === 'url' && res.url) {
      window.open(res.url, '_blank', 'noopener')
      return
    }
    if (res.message) toast.info(res.message)
    // 回调可能让机器人发来新消息或改消息，刷新一次。
    await loadMessages()
  } catch (e: unknown) {
    notifyApiError(e, 'chats.pressButtonFailed')
  } finally {
    pressingButton.value = false
  }
}

const selectedDialog = computed(
  () => dialogs.value.find((d) => String(d.chat_id) === String(selectedChatId.value)) ?? null,
)

const chatTypeLabel = (type: string): string => {
  switch ((type || '').toLowerCase()) {
    case 'private': return t('chats.typePrivate')
    case 'bot': return t('chats.typeBot')
    case 'group': return t('chats.typeGroup')
    case 'supergroup': return t('chats.typeSupergroup')
    case 'channel': return t('chats.typeChannel')
    default: return type || ''
  }
}

const formatDateTime = (s: string | null | undefined): string =>
  s ? s.slice(0, 16).replace('T', ' ') : ''

const scrollMessagesToBottom = async () => {
  await nextTick()
  const el = messagesBox.value
  if (el) el.scrollTop = el.scrollHeight
}

const loadAccounts = async () => {
  try {
    const list = await accountsStore.refreshAccounts()
    accountNames.value = list.map((acc) => acc.name)
    const wanted = String(route.query.account || '')
    if (wanted && accountNames.value.includes(wanted)) {
      selectedAccount.value = wanted
    } else if (!selectedAccount.value && accountNames.value.length > 0) {
      selectedAccount.value = accountNames.value[0]
    }
  } catch (e: unknown) {
    devLog.error('Failed to fetch accounts for chats', e)
    notifyApiError(e, 'chats.loadAccountsFailed')
  }
}

const loadDialogs = async () => {
  const token = getAuthToken()
  if (!token || !selectedAccount.value) return
  dialogsLoading.value = true
  dialogsError.value = false
  try {
    const res = await listChatDialogs(token, selectedAccount.value)
    dialogs.value = res.dialogs
    if (selectedChatId.value !== null && !dialogs.value.some((d) => String(d.chat_id) === String(selectedChatId.value))) {
      selectedChatId.value = null
      messages.value = []
    }
  } catch (e: unknown) {
    dialogsError.value = true
    notifyApiError(e, 'chats.loadDialogsFailed')
  } finally {
    dialogsLoading.value = false
  }
}

const loadMessages = async () => {
  const token = getAuthToken()
  if (!token || !selectedAccount.value || selectedChatId.value === null) return
  messagesLoading.value = true
  messagesError.value = false
  try {
    const res = await listChatMessages(token, selectedAccount.value, selectedChatId.value)
    messages.value = sortChatMessagesAsc(res.messages)
    await scrollMessagesToBottom()
    void loadMediaThumbs()
  } catch (e: unknown) {
    messagesError.value = true
    notifyApiError(e, 'chats.loadMessagesFailed')
  } finally {
    messagesLoading.value = false
  }
}

const selectChat = (dialog: ChatDialog) => {
  selectedChatId.value = dialog.chat_id
  messages.value = []
  clearMediaUrls()
  void loadMessages()
}

const onAccountChange = (e: Event) => {
  selectedAccount.value = (e.target as HTMLSelectElement).value
  selectedChatId.value = null
  messages.value = []
  dialogs.value = []
  clearMediaUrls()
  router.replace({ name: 'chats', query: { account: selectedAccount.value } })
  void loadDialogs()
}

const handleSend = async () => {
  const token = getAuthToken()
  const text = draft.value.trim()
  if (!token || !selectedAccount.value || selectedChatId.value === null || !text || sending.value) return
  sending.value = true
  try {
    const res = await sendChatMessage(token, selectedAccount.value, selectedChatId.value, text)
    draft.value = ''
    messages.value = sortChatMessagesAsc([...messages.value, res.message])
    await scrollMessagesToBottom()
  } catch (e: unknown) {
    notifyApiError(e, 'chats.sendFailed')
  } finally {
    sending.value = false
  }
}

onMounted(async () => {
  await loadAccounts()
  await loadDialogs()
})

onUnmounted(() => {
  clearMediaUrls()
})
</script>

<template>
  <div class="space-y-4">
    <div class="ui-card flex flex-wrap items-center gap-3 p-3">
      <label class="flex items-center gap-1.5 text-xs text-[var(--sp-text-muted)]">
        {{ t('chats.selectAccount') }}
        <select :value="selectedAccount" class="ui-input !h-8 !w-auto !text-xs" :aria-label="t('chats.selectAccount')" @change="onAccountChange">
          <option v-for="name in accountNames" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <button
        type="button"
        class="ui-btn-secondary !px-2.5 !py-1.5 !text-xs inline-flex items-center gap-1 cursor-pointer"
        :disabled="dialogsLoading || !selectedAccount"
        @click="loadDialogs"
      >
        <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': dialogsLoading }" />
        {{ t('chats.refresh') }}
      </button>
      <span v-if="selectedDialog" class="text-xs text-[var(--sp-text-muted)] truncate">
        {{ selectedDialog.title }}
        <span v-if="chatTypeLabel(selectedDialog.type)">· {{ chatTypeLabel(selectedDialog.type) }}</span>
      </span>
    </div>

    <div v-if="accountNames.length === 0" class="ui-empty !py-12">
      <div class="ui-empty-icon">
        <MessageSquareText class="w-8 h-8" />
      </div>
      <p class="ui-empty-desc">{{ t('chats.noAccount') }}</p>
    </div>

    <div v-else class="grid grid-cols-1 lg:grid-cols-[20rem_1fr] gap-4 items-start">
      <!-- 对话列表 -->
      <div class="ui-card divide-y divide-[var(--sp-border)] overflow-hidden">
        <div class="px-4 py-2.5 text-xs font-medium text-[var(--sp-text-muted)]">
          {{ t('chats.dialogs') }}<span v-if="dialogs.length > 0" class="font-mono">（{{ dialogs.length }}）</span>
        </div>
        <div v-if="dialogsLoading" class="px-4 py-6 text-xs text-[var(--sp-text-muted)] text-center">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="dialogsError" class="px-4 py-6 text-center">
          <p class="text-xs text-rose-600 dark:text-rose-400 mb-2">{{ t('chats.loadDialogsFailed') }}</p>
          <button type="button" class="ui-btn-secondary !px-3 !py-1.5 !text-xs cursor-pointer" @click="loadDialogs">{{ t('common.retry') }}</button>
        </div>
        <div v-else-if="dialogs.length === 0" class="px-4 py-6 text-xs text-[var(--sp-text-muted)] text-center">
          {{ t('chats.noDialogs') }}
        </div>
        <template v-else>
          <button
            v-for="dialog in dialogs"
            :key="String(dialog.chat_id)"
            type="button"
            class="w-full text-left px-4 py-3 cursor-pointer transition-colors"
            :class="String(selectedChatId) === String(dialog.chat_id) ? 'bg-sky-500/10 dark:bg-sky-500/10' : 'hover:bg-black/[0.03] dark:hover:bg-white/[0.04]'"
            @click="selectChat(dialog)"
          >
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-sm font-medium text-[var(--sp-text)] truncate flex-1">{{ dialog.title }}</span>
            <span v-if="dialog.unread_count > 0" class="shrink-0 min-w-5 h-5 px-1 rounded-full bg-sky-500 text-white text-[11px] leading-5 text-center font-mono">{{ dialog.unread_count }}</span>
          </div>
          <div class="text-xs text-[var(--sp-text-muted)] truncate mt-0.5">
            <span v-if="chatTypeLabel(dialog.type)">{{ chatTypeLabel(dialog.type) }} · </span>{{ truncateDialogPreview(dialog.last_message_text) }}
          </div>
          <div v-if="dialog.last_message_date" class="text-[11px] text-[var(--sp-text-muted)] mt-0.5 font-mono">
            {{ formatDateTime(dialog.last_message_date) }}
          </div>
          </button>
        </template>
      </div>

      <!-- 消息窗 -->
      <div class="ui-card flex flex-col overflow-hidden">
        <div v-if="selectedChatId === null" class="px-4 py-16 text-center">
          <MessageSquareText class="w-8 h-8 mx-auto text-[var(--sp-text-muted)]" />
          <p class="ui-empty-desc mt-3">{{ t('chats.selectChatHint') }}</p>
        </div>
        <template v-else>
          <div ref="messagesBox" class="flex-1 min-h-96 max-h-[60vh] overflow-y-auto px-4 py-3 space-y-2">
            <div v-if="messagesLoading" class="py-6 text-xs text-[var(--sp-text-muted)] text-center">
              {{ t('common.loading') }}
            </div>
            <div v-else-if="messagesError" class="py-6 text-center">
              <p class="text-xs text-rose-600 dark:text-rose-400 mb-2">{{ t('chats.loadMessagesFailed') }}</p>
              <button type="button" class="ui-btn-secondary !px-3 !py-1.5 !text-xs cursor-pointer" @click="loadMessages">{{ t('common.retry') }}</button>
            </div>
            <div v-else-if="messages.length === 0" class="py-6 text-xs text-[var(--sp-text-muted)] text-center">
              {{ t('chats.noMessages') }}
            </div>
            <div
              v-for="msg in messages"
              :key="msg.id ?? `${msg.date}-${msg.text.slice(0, 8)}`"
              class="flex"
              :class="msg.outgoing ? 'justify-end' : 'justify-start'"
            >
              <div
                class="max-w-[80%] rounded-[var(--sp-radius)] px-3 py-2 text-sm leading-relaxed break-words"
                :class="msg.outgoing
                  ? 'bg-sky-500 text-white'
                  : 'bg-black/[0.04] dark:bg-white/[0.06] text-[var(--sp-text)]'"
              >
                <div v-if="!msg.outgoing && msg.sender_name" class="text-[11px] opacity-70 mb-0.5">{{ msg.sender_name }}</div>
                <button
                  v-if="msg.media_type === 'photo' && mediaThumbUrl(msg)"
                  type="button"
                  class="block mb-1.5 cursor-pointer"
                  :aria-label="t('chats.viewFullImage')"
                  :title="t('chats.viewFullImage')"
                  @click="openFullImage(msg)"
                >
                  <img :src="mediaThumbUrl(msg)" :alt="t('chats.photoMessage')" class="max-w-full rounded-md">
                </button>
                <div v-else-if="msg.media_type === 'photo'" class="text-xs opacity-70 mb-1.5">{{ t('chats.imageLoadFailed') }}</div>
                <div class="whitespace-pre-wrap">{{ msg.text }}</div>
                <div v-if="msg.buttons && msg.buttons.length > 0" class="flex flex-wrap gap-1.5 mt-2">
                  <button
                    v-for="button in msg.buttons"
                    :key="`${button.row}-${button.col}`"
                    type="button"
                    class="px-2.5 py-1 rounded-full text-xs border cursor-pointer transition-colors"
                    :class="msg.outgoing
                      ? 'border-white/50 text-white hover:bg-white/15'
                      : 'border-sky-500/60 text-sky-600 dark:text-sky-400 hover:bg-sky-500/10'"
                    :disabled="pressingButton"
                    @click="handlePressButton(msg, button)"
                  >
                    {{ button.text }}
                  </button>
                </div>
                <div class="text-[11px] opacity-70 mt-1 font-mono text-right">{{ formatDateTime(msg.date) }}</div>
              </div>
            </div>
          </div>
          <div class="border-t border-[var(--sp-border)] p-3 flex items-center gap-2">
            <input
              v-model="draft"
              type="text"
              class="ui-input flex-1 !h-9 !text-sm"
              :placeholder="t('chats.typeMessage')"
              :aria-label="t('chats.typeMessage')"
              maxlength="4096"
              @keydown.enter.prevent="handleSend"
            >
            <button
              type="button"
              class="ui-btn-primary !px-3 !py-2 !text-xs inline-flex items-center gap-1 cursor-pointer"
              :disabled="sending || !draft.trim()"
              @click="handleSend"
            >
              <Send class="w-3.5 h-3.5" />
              {{ t('chats.send') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- 图片原图查看 -->
  <div
    v-if="fullImageUrl"
    class="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4 cursor-pointer"
    role="dialog"
    :aria-label="t('chats.viewFullImage')"
    @click="closeFullImage"
  >
    <button
      type="button"
      class="absolute top-4 right-4 text-white cursor-pointer"
      :aria-label="t('common.close')"
      @click="closeFullImage"
    >
      <X class="w-6 h-6" />
    </button>
    <img :src="fullImageUrl" :alt="t('chats.photoMessage')" class="max-h-full max-w-full object-contain">
  </div>
</template>
