/**
 * Panel ↔ FastAPI CustomerRead (backend/schemas.py CustomerBase).
 */

export interface CustomerRead {
  id: number
  profile_id: number | null
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
  /** BLS jurisdiction adı (jurisdictionData Name) */
  city: string
  bls_jurisdiction_id: string
  /** locationData Id */
  bls_office_code: string
  /** categoryData Code */
  appointment_category: string
  /** visaIdData Id */
  bls_visa_type_id: string
  /** visasubIdData Id */
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
