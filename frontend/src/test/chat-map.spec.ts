import { describe, it, expect } from 'vitest'
import { sortChatMessagesAsc, truncateDialogPreview } from '../lib/chat-map'
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
})
