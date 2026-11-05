/**
 * 账号管理 API：登录流程、CRUD、状态检测、设备、官方消息、账号日志。
 */
import {
  LONG_TIMEOUT_MS,
  MEDIUM_TIMEOUT_MS,
  createRequestAbort,
  fetchWithAuth,
  request,
  requestBlob,
  requestFormData,
} from "./core";
import { downloadBlob, normalizeNetworkError } from "../download";

export interface LoginStartRequest {
  account_name: string;
  phone_number: string;
  proxy?: string;
}

export interface LoginStartResponse {
  phone_code_hash: string;
  phone_number: string;
  account_name: string;
  message: string;
}

export interface LoginVerifyRequest {
  account_name: string;
  phone_number: string;
  phone_code: string;
  phone_code_hash: string;
  password?: string;
  proxy?: string;
}

export interface LoginVerifyResponse {
  success: boolean;
  user_id?: number;
  first_name?: string;
  username?: string;
  message: string;
}

export interface QrLoginStartRequest {
  account_name: string;
  proxy?: string;
}

export interface QrLoginStartResponse {
  login_id: string;
  qr_uri: string;
  qr_image?: string | null;
  expires_at: string;
}

export interface QrLoginStatusResponse {
  status: string;
  expires_at?: string;
  message?: string;
  account?: AccountInfo | null;
  user_id?: number;
  first_name?: string;
  username?: string;
}

export interface QrLoginCancelResponse {
  success: boolean;
  message: string;
}

export interface QrLoginPasswordRequest {
  login_id: string;
  password: string;
}

export interface QrLoginPasswordResponse {
  success: boolean;
  message: string;
  account?: AccountInfo | null;
  user_id?: number;
  first_name?: string;
  username?: string;
}

export interface DeviceProfile {
  device_model?: string;
  system_version?: string;
  app_version?: string;
  lang_code?: string;
  system_lang_code?: string;
}

export interface AccountInfo {
  name: string;
  session_file: string;
  exists: boolean;
  size: number;
  remark?: string | null;
  proxy?: string | null;
  status?: "connected" | "invalid" | "checking" | "error" | string;
  status_message?: string | null;
  status_code?: string | null;
  status_checked_at?: string | null;
  needs_relogin?: boolean;
  tags?: string[];
  device_family?: string | null;
  device_profile?: DeviceProfile | null;
  /** 代理页 / 账号卡展示用（后端 /api/accounts 扩展字段，缺失时省略对应行） */
  user_id?: number | null;
  dc_id?: number | null;
  dc_location?: string | null;
  phone?: string | null;
  username?: string | null;
  first_name?: string | null;
  proxy_mode?: "inherit" | "direct" | "pool" | "custom" | string | null;
  proxy_id?: string | null;
  proxy_label?: string | null;
  added_at?: string | null;
  /** 自定义顺序（Batch 2，服务端持久化）与暂停标志（Batch 5） */
  sort_order?: number | null;
  paused?: boolean;
}

export interface AccountStatusCheckRequest {
  account_names?: string[];
  timeout_seconds?: number;
}

export interface AccountStatusItem {
  account_name: string;
  ok: boolean;
  status: "connected" | "invalid" | "error" | "not_found" | string;
  message?: string;
  code?: string;
  checked_at?: string;
  needs_relogin?: boolean;
  user_id?: number;
}

export interface AccountStatusCheckResponse {
  results: AccountStatusItem[];
}

export interface AccountDeviceInfo {
  hash: string;
  current: boolean;
  official_app: boolean;
  password_pending: boolean;
  device_model: string;
  platform: string;
  system_version: string;
  app_name: string;
  app_version: string;
  date_created?: string | null;
  date_active?: string | null;
  ip: string;
  country: string;
  region: string;
}

export interface OfficialMessageInfo {
  id?: number | null;
  date?: string | null;
  text: string;
  outgoing: boolean;
}

export const startAccountLogin = (token: string, data: LoginStartRequest) =>
  request<LoginStartResponse>("/accounts/login/start", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

export const verifyAccountLogin = (token: string, data: LoginVerifyRequest) =>
  request<LoginVerifyResponse>("/accounts/login/verify", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

export const listAccounts = (token: string) =>
  request<{ accounts: AccountInfo[]; total: number }>("/accounts", {}, token);

export const checkAccountsStatus = (token: string, data: AccountStatusCheckRequest) =>
  request<AccountStatusCheckResponse>(
    "/accounts/status/check",
    { method: "POST", body: JSON.stringify(data) },
    token,
    MEDIUM_TIMEOUT_MS,
  );

/** 异步批量状态检测 Job */
export interface AccountStatusJob {
  job_id: string;
  kind: string;
  status: "running" | "canceling" | "canceled" | "completed" | "failed" | string;
  created_at?: string;
  updated_at?: string;
  finished_at?: string | null;
  progress?: { total?: number; done?: number; ok?: number; fail?: number };
  summary?: { total?: number; checked?: number; ok?: number; fail?: number };
  results?: AccountStatusItem[];
  logs?: Array<{ time?: string; level?: string; message?: string; ref?: string | null }>;
  error?: string | null;
  payload?: { account_names?: string[]; timeout_seconds?: number };
}

export const startAccountStatusCheckJob = (
  token: string,
  data: AccountStatusCheckRequest,
) =>
  request<AccountStatusJob>("/accounts/status/check-jobs", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

export const getAccountStatusCheckJob = (token: string, jobId: string) =>
  request<AccountStatusJob>(
    `/accounts/status/check-jobs/${encodeURIComponent(jobId)}`,
    {},
    token,
  );

export const listAccountStatusCheckJobs = (token: string, limit = 10) =>
  request<{ jobs: AccountStatusJob[] }>(
    `/accounts/status/check-jobs?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
  );

export const cancelAccountStatusCheckJob = (token: string, jobId: string) =>
  request<{ ok: boolean; job_id: string }>(
    `/accounts/status/check-jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" },
    token,
  );

export const deleteAccount = (token: string, accountName: string) =>
  request<{ success: boolean; message: string }>(`/accounts/${accountName}`, {
    method: "DELETE",
  }, token);

export const listAccountDevices = (token: string, accountName: string) =>
  request<{ devices: AccountDeviceInfo[]; total: number }>(
    `/accounts/${encodeURIComponent(accountName)}/devices`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export const terminateAccountDevice = (token: string, accountName: string, authHash: string) =>
  request<{ success: boolean; message: string }>(`/accounts/${encodeURIComponent(accountName)}/devices/${encodeURIComponent(authHash)}`, {
    method: "DELETE",
  }, token);

export const resetOtherDevices = (token: string, accountName: string) =>
  request<{ success: boolean; message: string }>(
    `/accounts/${encodeURIComponent(accountName)}/devices/reset-others`,
    {
      method: "POST",
    },
    token,
    MEDIUM_TIMEOUT_MS,
  );


export interface StandaloneSessionExportRequest {
  device_model?: string;
  timeout_seconds?: number;
}

export interface StandaloneSessionExportResponse {
  success: boolean;
  session_string?: string | null;
  dc_id?: number | null;
  user_id?: number | null;
  message: string;
}

export const exportStandaloneSession = (
  token: string,
  accountName: string,
  data?: StandaloneSessionExportRequest,
) =>
  request<StandaloneSessionExportResponse>(
    `/accounts/${encodeURIComponent(accountName)}/session-exports`,
    {
      method: "POST",
      body: JSON.stringify(data || {}),
    },
    token,
    LONG_TIMEOUT_MS,
  );

/** 会话保留时长（Telegram 原生 TTL，服务端执行） */
export interface SessionSettings {
  authorization_ttl_days?: number | null;
  account_ttl_days?: number | null;
}

export const getSessionSettings = (token: string, accountName: string) =>
  request<SessionSettings>(
    `/accounts/${encodeURIComponent(accountName)}/session-settings`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export const updateSessionSettings = (
  token: string,
  accountName: string,
  data: { authorization_ttl_days?: number | null; account_ttl_days?: number | null },
) =>
  request<SessionSettings>(
    `/accounts/${encodeURIComponent(accountName)}/session-settings`,
    { method: "PUT", body: JSON.stringify(data) },
    token,
    MEDIUM_TIMEOUT_MS,
  );

/**
 * 导出会话包 ZIP（原样复制当前 auth_key）。面板密码二次确认；
 * 成功后按服务端 Content-Disposition 文件名下载，失败时抛 ApiError
 * （detail 为 PANEL_PASSWORD_WRONG / 429 限流等后端错误码原文）。
 */
export async function exportSessionPackage(
  token: string,
  accountName: string,
  password: string,
): Promise<{ filename: string }> {
  const abort = createRequestAbort(LONG_TIMEOUT_MS, null);
  try {
    const res = await fetchWithAuth(
      `/account-tools/${encodeURIComponent(accountName)}/export-session`,
      { "Content-Type": "application/json" },
      { method: "POST", body: JSON.stringify({ password }), signal: abort.signal },
      token,
      LONG_TIMEOUT_MS,
    );
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = /filename="?([^"]+)"?/.exec(cd);
    const filename = match?.[1] || `${accountName}.zip`;
    downloadBlob(blob, filename);
    return { filename };
  } catch (e: unknown) {
    throw normalizeNetworkError(e, abort);
  } finally {
    abort.cleanup();
  }
}

export const listAccountOfficialMessages = (token: string, accountName: string, limit = 20) =>
  request<{ messages: OfficialMessageInfo[]; total: number }>(
    `/accounts/${encodeURIComponent(accountName)}/official-messages?limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

/** 刷新账号资料：调 get_me 重拉身份并更新缓存 */
export const refreshAccountProfile = (token: string, accountName: string) =>
  request<{ success: boolean; message: string; account?: AccountInfo | null }>(
    `/accounts/${encodeURIComponent(accountName)}/refresh-profile`,
    { method: "POST" },
    token,
    MEDIUM_TIMEOUT_MS,
  );

/** 上移 / 下移账号（自定义顺序持久化） */
export const moveAccount = (
  token: string,
  accountName: string,
  direction: "up" | "down",
) =>
  request<{ success: boolean; moved: boolean; message: string; account?: AccountInfo | null }>(
    `/accounts/${encodeURIComponent(accountName)}/move`,
    { method: "POST", body: JSON.stringify({ direction }) },
    token,
  );

/** 暂停 / 恢复账号 */
export const pauseAccount = (token: string, accountName: string) =>
  request<{ success: boolean; message: string; account?: AccountInfo | null }>(
    `/accounts/${encodeURIComponent(accountName)}/pause`,
    { method: "POST" },
    token,
  );

export const resumeAccount = (token: string, accountName: string) =>
  request<{ success: boolean; message: string; account?: AccountInfo | null }>(
    `/accounts/${encodeURIComponent(accountName)}/resume`,
    { method: "POST" },
    token,
  );

export const updateAccount = (
  token: string,
  accountName: string,
  data: {
    new_account_name?: string | null;
    remark?: string | null;
    proxy?: string | null;
    proxy_mode?: "inherit" | "direct" | "pool" | "custom" | null;
    proxy_id?: string | null;
    tags?: string[];
    device_family?: string | null;
    device_profile?: DeviceProfile | null;
  }
) =>
  request<{ success: boolean; message: string; account?: AccountInfo | null }>(`/accounts/${accountName}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  }, token);

export const startQrLogin = (token: string, data: QrLoginStartRequest) =>
  request<QrLoginStartResponse>("/accounts/qr/start", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

export const getQrLoginStatus = (token: string, loginId: string) =>
  request<QrLoginStatusResponse>(`/accounts/qr/status?login_id=${encodeURIComponent(loginId)}`, {}, token);

export const cancelQrLogin = (token: string, loginId: string) =>
  request<QrLoginCancelResponse>("/accounts/qr/cancel", {
    method: "POST",
    body: JSON.stringify({ login_id: loginId }),
  }, token);

export const submitQrPassword = (token: string, data: QrLoginPasswordRequest) =>
  request<QrLoginPasswordResponse>("/accounts/qr/password", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

// ─── 账号日志 ───

export interface AccountLog {
  id: number;
  account_name: string;
  task_name: string;
  message: string;
  summary?: string;
  bot_message?: string;
  success: boolean;
  created_at: string;
  failure_category?: string | null;
}

export const getRecentAccountLogs = (token: string, limit: number = 50) =>
  request<AccountLog[]>(`/accounts/logs/recent?limit=${limit}`, {}, token);

/**
 * 下载账号头像。复用 requestBlob 的鉴权与 401 跳转；失败时抛 ApiError，
 * 调用方按需 catch 回退到默认头像。
 */
export const fetchAccountAvatar = (token: string, accountName: string) =>
  requestBlob(`/accounts/${encodeURIComponent(accountName)}/avatar`, {}, token);

/**
 * 导出指定账号的历史运行日志（TXT）。
 * 服务端在 Content-Disposition 中给出已清洗的文件名，优先采用该名称，缺失时回退到账号名。
 */
export async function downloadAccountLogs(
  token: string,
  accountName: string,
): Promise<{ filename: string }> {
  const abort = createRequestAbort(LONG_TIMEOUT_MS, null);
  try {
    const res = await fetchWithAuth(
      `/accounts/${encodeURIComponent(accountName)}/logs/export`,
      {},
      { signal: abort.signal },
      token,
      null,
    );
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = /filename="?([^"]+)"?/.exec(cd);
    const filename = match?.[1] || `${accountName}_logs.txt`;
    downloadBlob(blob, filename);
    return { filename };
  } catch (e: unknown) {
    throw normalizeNetworkError(e, abort);
  } finally {
    abort.cleanup();
  }
}

// ─── 会话导入 ───

export interface ImportSessionRequest {
  account_name: string;
  session_type?: "auto" | "string" | "file";
  session_content?: string;
  force?: boolean;
  proxy?: string;
  tdata_password?: string;
}

export interface ImportSessionResponse {
  success: boolean;
  account_name: string;
  user_id?: number | null;
  first_name?: string | null;
  username?: string | null;
  message: string;
}

export const importAccountSession = (token: string, data: ImportSessionRequest) =>
  request<ImportSessionResponse>("/accounts/import-session", {
    method: "POST",
    body: JSON.stringify(data),
  }, token);

export const importAccountSessionFile = async (
  token: string,
  accountName: string,
  file: File | Blob,
  force = false,
  proxy?: string,
  tdataPassword?: string,
): Promise<ImportSessionResponse> => {
  const formData = new FormData();
  formData.append("account_name", accountName);
  formData.append("session_type", "file");
  formData.append("force", String(force));
  if (proxy) formData.append("proxy", proxy);
  if (tdataPassword) formData.append("tdata_password", tdataPassword);
  formData.append("file", file);

  return requestFormData<ImportSessionResponse>(
    "/accounts/import-session",
    formData,
    token,
    LONG_TIMEOUT_MS,
  );
};

// ─── KeePassXC 通行密钥登录（fork 补丁） ───

export interface PasskeyStatusResponse {
  ok: boolean;
  passkey_available: boolean;
  telethon_version?: string | null;
  api_is_custom: boolean;
  proxy_supported: string[];
}

export interface PasskeyLoginRequest {
  credential_id: string;
  private_key_pem: string;
  user_handle: string;
  api_id?: number;
  api_hash?: string;
  proxy?: string;
  account_name?: string;
  remark?: string;
  password?: string;
}

export interface PasskeyLoginResponse {
  success?: boolean;
  need_password?: boolean;
  passkey_id?: string;
  account_name?: string;
  user_id?: number | null;
  first_name?: string | null;
  username?: string | null;
  message?: string;
  api_is_custom?: boolean;
}

export const getPasskeyStatus = (token: string) =>
  request<PasskeyStatusResponse>("/passkey/status", {}, token);

export const passkeyLogin = (token: string, data: PasskeyLoginRequest) =>
  request<PasskeyLoginResponse>("/passkey/login", {
    method: "POST",
    body: JSON.stringify(data),
  }, token, LONG_TIMEOUT_MS);

export const submitPasskeyPassword = (
  token: string,
  data: { passkey_id: string; password: string },
) =>
  request<PasskeyLoginResponse>("/passkey/password", {
    method: "POST",
    body: JSON.stringify(data),
  }, token, LONG_TIMEOUT_MS);

// ─── 对话文件夹与论坛话题 ───

export interface FolderItem {
  id: number | string;
  title: string;
  emoticon?: string | null;
  pinned_peers?: (number | string)[];
  include_peers?: (number | string)[];
  exclude_peers?: (number | string)[];
  contacts?: boolean;
  non_contacts?: boolean;
  groups?: boolean;
  broadcasts?: boolean;
  bots?: boolean;
  exclude_muted?: boolean;
  exclude_read?: boolean;
  exclude_archived?: boolean;
}

export interface TopicItem {
  id: number;
  title: string;
  icon_color?: number | null;
  icon_emoji_id?: number | string | null;
  top_message?: number | null;
  closed?: boolean;
  pinned?: boolean;
  hidden?: boolean;
}

export const listAccountFolders = (token: string, accountName: string) =>
  request<FolderItem[]>(
    `/accounts/${encodeURIComponent(accountName)}/folders`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export const listForumTopics = (
  token: string,
  accountName: string,
  chatId: number | string,
  limit: number = 100,
) =>
  request<TopicItem[]>(
    `/accounts/${encodeURIComponent(accountName)}/chats/${encodeURIComponent(String(chatId))}/topics?limit=${limit}`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export interface AccountChangePlan {
  operation: 'delete' | 'rename';
  old_name: string;
  new_name: string | null;
  affected_tasks: { name: string; path: string; owned: boolean }[];
  affected_jobs: string[];
  affected_monitors: string[];
  history_policy: 'retain' | 'delete';
  history_paths: string[];
  plugin_namespaces: string[];
  reversible_steps: string[];
  warnings: string[];
  revision: string;
}

export const previewAccountChange = (
  token: string,
  accountName: string,
  operation: 'delete' | 'rename',
  newName?: string,
  historyPolicy: 'retain' | 'delete' = 'retain',
) => request<AccountChangePlan>(
  `/accounts/${encodeURIComponent(accountName)}/lifecycle/preview`,
  { method: 'POST', body: JSON.stringify({ operation, new_name: newName, history_policy: historyPolicy }) },
  token,
);

export const applyAccountChange = (token: string, plan: AccountChangePlan) =>
  request<{ success: boolean; account_name: string; history_policy: string }>(
    `/accounts/${encodeURIComponent(plan.old_name)}/lifecycle/apply`,
    { method: 'POST', body: JSON.stringify({ plan }) },
    token,
  );
