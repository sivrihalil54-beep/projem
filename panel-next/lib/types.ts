/** FastAPI müşteri / profil sözleşmesi */

export interface CustomerRead {
  id: number
  profile_id: number | null
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  city: string
  bls_jurisdiction_id: string
  bls_office_code: string
  appointment_category: string
  bls_visa_type_id: string
  visa_type: string
  live_status: string
  notes: string
  created_at: string
  updated_at: string
}

export type CustomerUpsertPayload = {
  profile_id?: number | null
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  city: string
  bls_jurisdiction_id: string
  bls_office_code: string
  appointment_category: string
  bls_visa_type_id: string
  visa_type: string
  live_status?: string
  notes?: string
}

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
  password: string
  login_url: string
  gmail_app_password: string | null
  is_active: boolean
  run_count: number
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
  is_assigned: boolean
}

export interface BotLogsResponse {
  chunk: string
  next_offset: number
  seek_reset: boolean
  total_size: number
  has_more: boolean
  path_rel: string
}

export interface BotStatusResponse {
  running: boolean
  pid: number | null
}

export type NavKey = 'customers' | 'logs' | 'settings'
