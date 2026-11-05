/**
 * 部署后旧 chunk 失效的兜底：容器重建后 chunk 文件名按内容哈希变化，
 * 仍在跑旧 bundle 的标签页跳转懒加载路由时，动态 import 会 404，
 * vue-router 导航失败、页面空白卡死。这里检测到 chunk 加载失败就
 * 硬刷新一次取新版入口；用 sessionStorage 做 60 秒冷却，避免循环刷新。
 */

const RELOAD_FLAG_KEY = 'sp-chunk-reload-at'
const RELOAD_COOLDOWN_MS = 60_000

let reloadedThisPage = false

export function isChunkLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error ?? '')
  return (
    /Failed to fetch dynamically imported module|Loading chunk \d+ failed|Importing a module script failed/i.test(
      message,
    )
  )
}

export function reloadOnceForChunkError(): boolean {
  if (reloadedThisPage) return false
  reloadedThisPage = true
  try {
    const last = Number(sessionStorage.getItem(RELOAD_FLAG_KEY) || 0)
    if (Number.isFinite(last) && Date.now() - last < RELOAD_COOLDOWN_MS) {
      return false
    }
    sessionStorage.setItem(RELOAD_FLAG_KEY, String(Date.now()))
  } catch {
    // sessionStorage 不可用：本页内只刷新一次（模块级标记已置位）。
  }
  window.location.reload()
  return true
}
