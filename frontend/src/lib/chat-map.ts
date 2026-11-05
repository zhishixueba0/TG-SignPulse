/**
 * 聊天中心：消息排序与对话摘要（纯函数）。
 */
import type { ChatMessage } from './api'

/** 消息按时间升序：服务端已按升序返回，此处兜底保证展示顺序稳定。 */
export function sortChatMessagesAsc(messages: ChatMessage[]): ChatMessage[] {
  return [...messages]
    .map((message, index) => ({ message, index }))
    .sort((a, b) => {
      const ta = a.message.date ? Date.parse(a.message.date) : Number.NaN
      const tb = b.message.date ? Date.parse(b.message.date) : Number.NaN
      if (Number.isNaN(ta) && Number.isNaN(tb)) return a.index - b.index
      if (Number.isNaN(ta)) return 1
      if (Number.isNaN(tb)) return -1
      return ta - tb || a.index - b.index
    })
    .map(({ message }) => message)
}

/** 对话最后消息摘要过长时截断展示。 */
export function truncateDialogPreview(text: string, maxLength = 60): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= maxLength) return flat
  return `${flat.slice(0, maxLength)}…`
}
