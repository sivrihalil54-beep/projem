import type { Locator, Page } from '@playwright/test'

/**
 * BLS doğrula / gönder kontrolleri için erişilebilir ad kalıpları (`getByRole`) + `#btnVerify` + `input[type=submit]` yedekleri.
 * Bazı oturumlarda sunucu `button` yerine `input[type="submit"]` veya zayıf a11y yapısı kullanır.
 */
export const BLS_VERIFY_BUTTON_NAME_RE =
  /dogrula|doğrula|doğrulamak|dogrulamak|verify|verification|confirm|submit|gönder|gonder|send|tamam|continue|next|ileri|proceed|apply|basla|başla/i

/**
 * Doğrula / Verify bileşeni — öncelik rol tabanlı, sonra yapısal BLS seçicileri (`pages/bls_logincaptcha_page`).
 */
export function verifySubmitLocatorUnion(page: Page): Locator {
  const byAccessibleName = page.getByRole('button', {
    name: BLS_VERIFY_BUTTON_NAME_RE,
  })
  /** BLS `btnVerify`; `input` / `button` varyantları */
  const btnVerifyStructural = page.locator(
    '#btnVerify, input#btnVerify, button#btnVerify, [name="btnVerify"][type="submit"]',
  )
  /** `value` ile etiketlenen gönder (`[i]` XPath yok — çok kalıp ile kapsama). */
  const submitByValueHints = page.locator(
    [
      'input[type="submit"][value*="Verify"]',
      'input[type="submit"][value*="verify"]',
      'input[type="submit"][value*="Doğrula"]',
      'input[type="submit"][value*="Dogrula"]',
      'input[type="submit"][value*="DOG"]',
      'input[type="submit"][value*="Submit"]',
      'input[type="submit"][value*="submit"]',
      'input[type="submit"][value*="Tamam"]',
      'input[type="submit"][value*="tamam"]',
      'input[type="submit"][value*="Confirm"]',
      'input[type="submit"][value*="Send"]',
    ].join(', '),
  )
  /** Görünür düğüm metnine göre */
  const buttonSubmitTagged = page
    .locator('button[type="submit"]')
    .filter({ hasText: BLS_VERIFY_BUTTON_NAME_RE })

  return byAccessibleName
    .or(btnVerifyStructural)
    .or(submitByValueHints)
    .or(buttonSubmitTagged)
}
