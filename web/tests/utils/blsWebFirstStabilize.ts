import { expect, type Locator, type Page } from '@playwright/test'

import { verifySubmitLocatorUnion } from '../pages/blsVerifyLocators'

function readBudgetMs(defaultMs: number): number {
  const raw = (process.env.BLS_LOGIN_STABILIZE_BUDGET_MS ?? '').trim()
  if (!raw) return defaultMs
  const n = Number.parseInt(raw, 10)
  return Number.isFinite(n) && n >= 5_000 ? n : defaultMs
}

/**
 * Python `utils/playwright_web_first.wait_optional_visible` karşılığı.
 */
export async function waitOptionalVisible(
  locator: Locator,
  timeoutMs: number,
): Promise<boolean> {
  try {
    await expect(locator).toBeVisible({ timeout: timeoutMs })
    return true
  } catch {
    return false
  }
}

/**
 * Login formu hazır olduktan sonra: önce captcha kapsayıcısı; yoksa DOM / ağ sakinliği (`time.sleep` yok).
 *
 * Kaynak: `stabilize_after_login_form_ready`
 *
 * @param page Aktif sekme
 * @param captchaLocator Örn. `.captcha-wrapper` + img filtresi
 * @param overallTimeoutMs Üst süre (ms)
 */
export async function stabilizeAfterLoginFormReady(
  page: Page,
  captchaLocator: Locator,
  overallTimeoutMs: number,
): Promise<void> {
  if (await waitOptionalVisible(captchaLocator, overallTimeoutMs)) {
    return
  }
  try {
    await page.waitForLoadState('domcontentloaded', {
      timeout: Math.min(3_000, overallTimeoutMs),
    })
  } catch {
    /* devam — networkidle dene */
  }
  try {
    await page.waitForLoadState('networkidle', { timeout: overallTimeoutMs })
  } catch {
    /* yumuşak */
  }
}

/**
 * E-posta + Enter sonrası captcha tetik stabilizasyonu.
 *
 * Kaynak: `stabilize_after_captcha_trigger`
 */
export async function stabilizeAfterCaptchaTrigger(
  page: Page,
  captchaLocator: Locator,
  overallTimeoutMs: number,
): Promise<void> {
  await stabilizeAfterLoginFormReady(page, captchaLocator, overallTimeoutMs)
}

/** `default_step0_captcha_locator` — BLS için img içeren ilk captcha kutusu */
export function defaultStep0CaptchaLocator(
  page: Page,
  containerSelector?: string,
): Locator {
  const sel =
    (containerSelector && containerSelector.trim()) ||
    (typeof process.env.BLS_CAPTCHA_CONTAINER === 'string' &&
    process.env.BLS_CAPTCHA_CONTAINER.trim()
      ? process.env.BLS_CAPTCHA_CONTAINER.trim()
      : '.captcha-wrapper')
  return page.locator(sel).filter({ has: page.getByRole('img') }).first()
}

/**
 * `goto` sonrası doğrulanabilir gövde: DOM/load + doğrula kontrolünün DOM’a yapışması + isteğe bağlı captcha / networkidle.
 *
 * Böylece yalnızca `networkidle` veya tek captcha süresiyle yetinilmez; doğrula `input[type=submit]` ise erken yakalanır.
 *
 * @param page Aktif sekme
 * @param captchaLocator Görünür karı grid ipucu
 * @param overallTimeoutMs `BLS_LOGIN_STABILIZE_BUDGET_MS` ile override edilebilir alt sınır
 */
export async function stabilizeNavigationThenLoginAnchors(
  page: Page,
  captchaLocator: Locator,
  overallTimeoutMs?: number,
): Promise<void> {
  const budget = readBudgetMs(
    typeof overallTimeoutMs === 'number' && overallTimeoutMs > 0
      ? overallTimeoutMs
      : 38_000,
  )
  const verifyUnion = verifySubmitLocatorUnion(page)

  try {
    await page.waitForLoadState('domcontentloaded', {
      timeout: Math.min(28_000, budget),
    })
  } catch {
    /* yumuşak */
  }
  try {
    await page.waitForLoadState('load', { timeout: Math.min(22_000, budget) })
  } catch {
    /* Bazı SPA’lar `load` üretmez */
  }

  try {
    await expect(verifyUnion.first()).toBeAttached({
      timeout: Math.min(32_000, budget),
    })
  } catch {
    /* hâlâ stabilizasyon dene */
  }

  if (await waitOptionalVisible(captchaLocator, Math.min(9_000, budget))) {
    try {
      await expect(verifyUnion.first()).toBeAttached({
        timeout: Math.min(12_000, budget),
      })
    } catch {
      /* devam */
    }
    return
  }

  try {
    await page.waitForLoadState('networkidle', {
      timeout: Math.min(18_000, budget),
    })
  } catch {
    /* BLS sık sık sürekli XHR ile “idle” yakalamaz — devam */
  }

  await stabilizeAfterLoginFormReady(page, captchaLocator, Math.min(14_000, budget))

  try {
    await expect(verifyUnion.first()).toBeAttached({
      timeout: Math.min(20_000, budget),
    })
  } catch {
    /* son çağıran bekleyecek */
  }
}
