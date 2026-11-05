/**
 * 标签文本：空格分隔的标签输入解析与合并（纯函数）。
 *
 * 卡片“+ 添加标签”只追加不覆盖；“···”菜单“编辑标签”全量编辑，
 * 空格分隔、清空则移除全部。两处共用同一解析规则。
 */

/** 解析空格分隔的标签文本：去空、去重、保序。 */
export function parseTagsInput(text: string): string[] {
  const seen = new Set<string>()
  const tags: string[] = []
  for (const part of text.split(/\s+/)) {
    const tag = part.trim()
    if (!tag || seen.has(tag)) continue
    seen.add(tag)
    tags.push(tag)
  }
  return tags
}

/** 追加模式：现有标签 + 新标签，去重保序（现有在前）。 */
export function mergeTags(existing: string[], additions: string[]): string[] {
  return parseTagsInput([...existing, ...additions].join(' '))
}

/** 标签列表拼回输入框文本（全量编辑模式回填用）。 */
export function formatTagsInput(tags: string[]): string {
  return tags.join(' ')
}
