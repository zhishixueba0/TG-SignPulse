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

/** 消息的日期（YYYY-MM-DD），无日期返回空串。 */
export function messageDay(date: string | null | undefined): string {
  return date ? date.slice(0, 10) : ''
}

/** 当前消息前是否要放日期分隔条：首条有日期的消息、或与上一条不同天。 */
export function isNewDay(prev: ChatMessage | null | undefined, cur: ChatMessage): boolean {
  const day = messageDay(cur.date)
  if (!day) return false
  if (!prev) return true
  return messageDay(prev.date) !== day
}

/**
 * “以下为未读消息”分隔线的插入位置：未读消息之前那条的下标。
 * 未读数为 0 或消息为空返回 -1；未读数超过已加载条数时按全部未读处理。
 */
export function unreadDividerIndex(messages: ChatMessage[], unreadCount: number): number {
  const n = Math.max(0, Math.min(Math.floor(unreadCount || 0), messages.length))
  if (n <= 0) return -1
  return messages.length - n
}

/** 对话最后消息摘要过长时截断展示。 */
export function truncateDialogPreview(text: string, maxLength = 60): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= maxLength) return flat
  return `${flat.slice(0, maxLength)}…`
}
