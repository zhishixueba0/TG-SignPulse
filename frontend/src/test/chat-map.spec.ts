import { describe, it, expect } from 'vitest'
import { isNewDay, messageDay, sortChatMessagesAsc, truncateDialogPreview, unreadDividerIndex } from '../lib/chat-map'
import type { ChatMessage } from '../lib/api'

const msg = (over: Partial<ChatMessage>): ChatMessage => ({
  text: '',
  outgoing: false,
  sender_name: '',
  ...over,
})

describe('chat-map', () => {
  it('sorts messages ascending by date, stable for missing dates', () => {
    const list = [
      msg({ id: 3, date: '2026-11-05T10:02:00Z', text: 'c' }),
      msg({ id: 1, date: '2026-11-05T10:00:00Z', text: 'a' }),
      msg({ id: 2, date: '2026-11-05T10:01:00Z', text: 'b' }),
      msg({ id: 4, date: null, text: 'no-date' }),
    ]
    expect(sortChatMessagesAsc(list).map((m) => m.id)).toEqual([1, 2, 3, 4])
  })

  it('truncates long dialog previews', () => {
    expect(truncateDialogPreview('short')).toBe('short')
    expect(truncateDialogPreview('x'.repeat(100), 10)).toBe(`${'x'.repeat(10)}…`)
    expect(truncateDialogPreview('a  b\nc')).toBe('a b c')
  })

  it('detects day boundaries for date separators', () => {
    expect(messageDay('2026-11-06T10:00:00Z')).toBe('2026-11-06')
    expect(messageDay(null)).toBe('')
    expect(messageDay(undefined)).toBe('')
    const first = msg({ id: 1, date: '2026-11-06T10:00:00Z' })
    const sameDay = msg({ id: 2, date: '2026-11-06T11:00:00Z' })
    const nextDay = msg({ id: 3, date: '2026-11-07T09:00:00Z' })
    const noDate = msg({ id: 4, date: null })
    expect(isNewDay(null, first)).toBe(true)
    expect(isNewDay(first, sameDay)).toBe(false)
    expect(isNewDay(sameDay, nextDay)).toBe(true)
    expect(isNewDay(nextDay, noDate)).toBe(false)
  })

  it('places the unread divider before the last unread messages', () => {
    const list = [
      msg({ id: 1, date: '2026-11-06T10:00:00Z' }),
      msg({ id: 2, date: '2026-11-06T10:01:00Z' }),
      msg({ id: 3, date: '2026-11-06T10:02:00Z' }),
      msg({ id: 4, date: '2026-11-06T10:03:00Z' }),
    ]
    expect(unreadDividerIndex(list, 0)).toBe(-1)
    expect(unreadDividerIndex(list, 1)).toBe(3)
    expect(unreadDividerIndex(list, 2)).toBe(2)
    expect(unreadDividerIndex(list, 4)).toBe(0)
    expect(unreadDividerIndex(list, 99)).toBe(0)
    expect(unreadDividerIndex([], 2)).toBe(-1)
    expect(unreadDividerIndex(list, -1)).toBe(-1)
  })
})
