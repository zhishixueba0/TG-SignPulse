import { describe, it, expect } from 'vitest'
import { formatTagsInput, mergeTags, parseTagsInput } from '../lib/tag-text'

describe('tag-text', () => {
  it('parses space-separated tags, dedupes and drops empties', () => {
    expect(parseTagsInput('VIP  工作 VIP')).toEqual(['VIP', '工作'])
    expect(parseTagsInput('  ')).toEqual([])
    expect(parseTagsInput('a\tb\nc')).toEqual(['a', 'b', 'c'])
  })

  it('append mode merges without replacing existing tags', () => {
    expect(mergeTags(['VIP'], ['工作', 'VIP'])).toEqual(['VIP', '工作'])
    expect(mergeTags(['VIP'], [])).toEqual(['VIP'])
  })

  it('formats tags back to space-separated input text', () => {
    expect(formatTagsInput(['VIP', '工作'])).toBe('VIP 工作')
    expect(formatTagsInput([])).toBe('')
  })
})
