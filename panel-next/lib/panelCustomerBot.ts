/**
 * Bot / Playwright ile uyumlu panel sozlesmesi.
 * Kaynak: GET /api/customers/by-profile/{profileId}
 *
 * Ornek (Playwright JS/TS):
 *   const customer = await getCustomerData(baseUrl, profileId);
 *   await page.getByLabel(customer.playwright_hints.province_select_label)
 *     .selectOption({ label: customer.location.province_label });
 *   await page.getByRole('radio', { name: customer.visa.category_radio_name }).click();
 */

export interface PanelPersonal {
  first_name: string
  last_name: string
  tc_kimlik_no: string
  passport_no: string
  birth_date: string
}

export interface PanelLocation {
  city: string
  province_label: string
  bls_jurisdiction_id: string
  application_center_id: string
  application_center_name: string
  notes: string
}

export interface PanelVisa {
  category_code: string
  category_radio_name: string
  panel_category: string
  simplified_kind: string
  bls_visa_type_id: string
  visa_subtype_id: string
}

export interface PanelPlaywrightHints {
  province_select_label: string
  otp_placeholder: string
  otp_visible_timeout_ms: number
}

export interface PanelCustomerBotBundle {
  profile_id: number
  customer_id: number
  personal: PanelPersonal
  location: PanelLocation
  visa: PanelVisa
  playwright_hints: PanelPlaywrightHints
}

export async function getCustomerData(
  apiBase: string,
  profileId: number,
): Promise<PanelCustomerBotBundle> {
  const base = apiBase.replace(/\/$/, '')
  const res = await fetch(`${base}/api/customers/by-profile/${profileId}`)
  if (!res.ok) {
    const t = await res.text()
    throw new Error(t || `HTTP ${res.status}`)
  }
  return res.json() as Promise<PanelCustomerBotBundle>
}
