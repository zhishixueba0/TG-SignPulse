/**
 * 代理管理 API：代理池 CRUD、检测、订阅同步、账号路由与全局代理。
 * 契约与后端 /api/proxies 对齐；绝不传输或展示代理密码 / Secret，
 * 仅用 has_password / has_secret 标识是否存在。
 */
import { request } from "./core"

export type ProxyType = "socks5" | "socks4" | "http" | "mtproto"
export type ProxySource = "manual" | "freeproxydb"
export type ProxyMode = "inherit" | "direct" | "pool" | "custom"
export type ProxyJobStatus = "idle" | "running" | "done" | "error"

export interface ProxyItem {
  id: string
  label: string
  ptype: ProxyType
  host: string
  port: number
  has_password: boolean
  has_secret: boolean
  source: ProxySource
  country_code: string | null
  reg_country: string | null
  isp: string | null
  org: string | null
  is_hosting: boolean | null
  announced_asn: string | null
  announced_prefix: string | null
  latency_ms: number | null
  last_tested_at: string | null
  last_test_ok: boolean | null
  created_at: string | null
  display_url: string
  accounts_count: number
  enrich_pending: boolean
  // 解析 IP 快照：域名解析出的 IP（host 本身是 IP 时就是它自己）；
  // 解析失败或还没解析过为 null，resolved_at 标明快照时间
  resolved_ip: string | null
  resolved_at: string | null
}

export interface ProxyListResponse {
  proxies: ProxyItem[]
  total: number
}

export interface CreateProxyRequest {
  ptype: ProxyType
  host: string
  port: number
  username?: string
  password?: string
  secret?: string
  label?: string
  auto_name?: boolean
  region?: string | null
}

export interface CreateProxyResponse {
  success: boolean
  proxy: ProxyItem
  blocked?: boolean
  message: string
}

export interface ImportProxiesResponse {
  success: boolean
  added: number
  skipped: number
  blocked: number
  telegram_failed?: number
  added_ids: string[]
}

export interface BatchDeleteProxiesResponse {
  success: boolean
  deleted: number
  reset_accounts: string[]
}

export interface DeleteProxyResponse {
  success: boolean
  message: string
  reset_accounts: string[]
}

export interface RenameProxyResponse {
  success: boolean
  proxy: ProxyItem
}

export interface TestProxyResponse {
  success: boolean
  ok: boolean
  latency_ms: number | null
  detail: string
  last_test_ok: boolean | null
  /** TCP 端口是否接通（与可用与否无关）：端口通但握手失败时为 true。 */
  tcp_ok?: boolean
}

/** 卡片“复制链接”：后端按需返回单条节点的可分享链接（MTProto 为 tg://proxy 链接）。 */
export interface ProxyLinkResponse {
  link: string
}

/** 检测 / 清理类后台任务进度 */
export interface ProxyJobProgress {
  status: ProxyJobStatus
  total: number
  done: number
  ok: number
  fail: number
  error?: string | null
}

export interface ProxyCleanupJobProgress {
  status: ProxyJobStatus
  total: number
  done: number
  removed: number
  error?: string | null
}

export interface ProxySyncJobProgress {
  status: ProxyJobStatus
  fetched: number
  tested: number
  added: number
  kept: number
  removed: number
  blocked_removed: number
  telegram_failed: number
  error?: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ProxyRoute {
  account_name: string
  mode: ProxyMode
  proxy_id: string | null
  proxy_label: string | null
  effective_label: string
  online: boolean
}

export interface ProxyRoutesResponse {
  routes: ProxyRoute[]
  global_proxy: { set: boolean; display_url: string | null }
}

export interface GlobalProxyResponse {
  set: boolean
  display_url: string | null
}

export interface SetGlobalProxyResponse {
  success: boolean
  set: boolean
}

export interface TestGlobalProxyResponse {
  success: boolean
  ok: boolean
  detail: string
  tcp_ok?: boolean
}

export const listProxies = (token: string) =>
  request<ProxyListResponse>("/proxies", {}, token)

export const createProxy = (token: string, data: CreateProxyRequest) =>
  request<CreateProxyResponse>("/proxies", {
    method: "POST",
    body: JSON.stringify(data),
  }, token)

export const importProxies = (token: string, text: string) =>
  request<ImportProxiesResponse>("/proxies/import", {
    method: "POST",
    body: JSON.stringify({ text }),
  }, token)

export const batchDeleteProxies = (token: string, ids: string[]) =>
  request<BatchDeleteProxiesResponse>("/proxies/batch-delete", {
    method: "POST",
    body: JSON.stringify({ ids }),
  }, token)

export const deleteProxy = (token: string, id: string) =>
  request<DeleteProxyResponse>(`/proxies/${encodeURIComponent(id)}`, {
    method: "DELETE",
  }, token)

export const renameProxy = (token: string, id: string, label: string) =>
  request<RenameProxyResponse>(`/proxies/${encodeURIComponent(id)}/rename`, {
    method: "POST",
    body: JSON.stringify({ label }),
  }, token)

export const testProxy = (token: string, id: string) =>
  request<TestProxyResponse>(`/proxies/${encodeURIComponent(id)}/test`, {
    method: "POST",
  }, token)

export const getProxyLink = (token: string, id: string) =>
  request<ProxyLinkResponse>(`/proxies/${encodeURIComponent(id)}/link`, {}, token)

export const startTestAllProxies = (token: string, mode: "probe" | "tcp" = "probe") =>
  request<{ success: boolean; started: boolean }>("/proxies/test-all", {
    method: "POST",
    body: JSON.stringify({ mode }),
  }, token)

export const getTestAllProxiesJob = (token: string) =>
  request<ProxyJobProgress>("/proxies/test-all/job", {}, token)

export const startCleanupBlockedProxies = (token: string) =>
  request<{ success: boolean; started: boolean }>("/proxies/cleanup-blocked", {
    method: "POST",
  }, token)

export const getCleanupBlockedProxiesJob = (token: string) =>
  request<ProxyCleanupJobProgress>("/proxies/cleanup-blocked/job", {}, token)

export const getProxySubscription = (token: string) =>
  request<{ url: string | null; default_url: string }>("/proxies/subscription", {}, token)

export const saveProxySubscription = (token: string, url: string | null) =>
  request<{ success: boolean; url: string | null }>("/proxies/subscription", {
    method: "POST",
    body: JSON.stringify({ url }),
  }, token)

export const startProxySync = (token: string) =>
  request<{ success: boolean; started: boolean; already_running?: boolean }>("/proxies/sync", {
    method: "POST",
  }, token)

export const getProxySyncJob = (token: string) =>
  request<ProxySyncJobProgress>("/proxies/sync/job", {}, token)

export const listProxyRoutes = (token: string) =>
  request<ProxyRoutesResponse>("/proxies/routes", {}, token)

export const updateProxyRoute = (
  token: string,
  data: {
    account_name: string
    mode: ProxyMode
    proxy_id?: string | null
    proxy_url?: string
  },
) =>
  request<{ success: boolean; message: string }>("/proxies/routes", {
    method: "POST",
    body: JSON.stringify(data),
  }, token)

export const getGlobalProxy = (token: string) =>
  request<GlobalProxyResponse>("/proxies/global", {}, token)

export const setGlobalProxy = (token: string, proxyUrl: string | null) =>
  request<SetGlobalProxyResponse>("/proxies/global", {
    method: "POST",
    body: JSON.stringify({ proxy_url: proxyUrl }),
  }, token)

export const testGlobalProxy = (token: string) =>
  request<TestGlobalProxyResponse>("/proxies/global/test", {
    method: "POST",
  }, token)
