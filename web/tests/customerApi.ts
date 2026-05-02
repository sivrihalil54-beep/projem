/**
 * FastAPI `/api/customer/{id}` (ve mock) ile uyumlu musteri yuklemesi.
 *
 * Playwright `request` bagimsiz APIRequestContext kullanir ve `page.route`
 * mock'unu gormez; bu yuzden tarayici icinde fetch kullanilir.
 */

import type { Page } from '@playwright/test'

export type PanelCustomer = {
  id: number
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
  profile_id: number | null
  notes?: string
}

/**
 * Tarayici baglaminda `fetch` ile musteri GET — `page.route` mock'u uygulanir.
 *
 * @param page - Acik panel sayfasi (once goto yapilmis olmali)
 * @param id - Musteri kimligi
 * @returns `PanelCustomer` JSON
 */
export async function getCustomerFromApiInPage(
  page: Page,
  id: number,
): Promise<PanelCustomer> {
  return page.evaluate(async (cid: number) => {
    const r = await fetch(`/api/customer/${cid}`)
    const txt = await r.text()
    if (!r.ok) {
      throw new Error(`GET /api/customer/${cid} -> ${r.status} ${txt}`)
    }
    return JSON.parse(txt) as PanelCustomer
  }, id)
}
