/**
 * Panel ↔ FastAPI sözleşmesi (`backend/schemas.py` ile uyumlu).
 */

export interface ProxySummary {
  id: number
  scheme: string
  host: string
  port: number
  username: string
  password: string
  note: string
  fail_count: number
  lock_until: string
}

export interface ProfileRead {
  id: number
  label: string
  email: string
  /** Yerel SQLite — düz metin; liste ve düzenleme API üzerinden taşınır */
  password: string
  login_url: string
  gmail_app_password: string | null
  is_active: boolean
  run_count: number
  /** Panel/bot teşhisi (Son Hata) */
  last_error: string
  /** SQLite datetime('now') veya boş */
  last_error_at: string
  proxy: ProxySummary | null
}

export interface ProxyPoolRow {
  id: number
  scheme: string
  host: string
  port: number
  username: string
  password: string
  note: string
  assigned_profile_id: number | null
  assigned_profile_label: string | null
  is_assigned: boolean
  fail_count: number
  lock_until: string
  is_active: boolean
  last_used_at: string
}

export interface NewProfileForm {
  label: string
  email: string
  password: string
  gmail_app_password: string
  login_url: string
}

export interface EditProfileFormState {
  id: number
  label: string
  email: string
  /** Sunucudaki kayıt; boşaltılırsa PUT'ta password gönderilmez (mevcut korunur) */
  password: string
  gmail_app_password: string
  clearGmailAppPassword: boolean
  hasGmailAppPassword: boolean
  login_url: string
  assignProxyId: string
  _run_count: number
  /** Salt okunur — API last_error */
  lastErrorFromServer: string
  lastErrorAtFromServer: string
}

export interface ProxyEditFormState {
  id: number
  scheme: string
  host: string
  port: number | string
  username: string
  password: string
  note: string
}

export interface ProfileUpdatePayload {
  label?: string
  email?: string
  login_url?: string
  password?: string
  gmail_app_password?: string
  clear_gmail_app_password?: boolean
}

export interface BulkImportResponse {
  inserted?: number
  skipped_invalid?: number
}

export interface BulkDeleteResponse {
  deleted?: number
  requested?: number
  mode?: 'all' | 'ids'
}

export interface RotateAssignResponse {
  assigned_pairs?: number
  profiles_without_proxy?: number
  message?: string
  proxy_id?: number
  scheme?: string
  host?: string
  port?: number
}

export interface StartBotResponse {
  ok?: boolean
  message?: string
  pid?: number
  headless?: boolean
}

/** GET /api/bot/logs */
export interface BotLogsResponse {
  chunk: string
  next_offset: number
  seek_reset: boolean
  total_size: number
  has_more: boolean
  path_rel: string
}

/** GET /api/bot/status */
export interface BotStatusResponse {
  running: boolean
  pid: number | null
}

/** POST /api/bot/stop | /api/bot/reset */
export interface BotControlResponse {
  ok?: boolean
  stopped?: boolean
  pid?: number
  message?: string
  log_cleared?: boolean
  log_error?: string
}

export type StatusBanner = {
  kind: '' | 'ok' | 'err'
  text: string
  variant?: 'default' | 'critical'
}
