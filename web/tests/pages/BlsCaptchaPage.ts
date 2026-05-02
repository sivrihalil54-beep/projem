import { expect, type Locator, type Page } from '@playwright/test'

import type { CaptchaDatasetCollector } from '../utils/captchaDataset'

import { preprocessCaptcha, preprocessImage } from '../../src/utils/captcha-ocr-solver'
import {
  evaluateCaptchaOcrMatch,
  levenshteinDistance,
  logCaptchaTileClick,
  logOcrSmartMatch,
  sanitizeCaptchaDigits,
} from '../../src/utils/captcha-smart-match'

import { verifySubmitLocatorUnion } from './blsVerifyLocators'

/**
 * BLS login captcha görünümü (`pages/bls_logincaptcha_page.py` ile hizalı POM).
 * Frekans / karo CAPTCHA için obfuscation amaçı zorunlu CSS kalır; doğrula butonu rol tabanlıdır.
 */
export class BlsCaptchaPage {
  static readonly CAPTCHA_MAIN_DIV = '#captcha-main-div'

  static readonly CAPTCHA_TILE = 'img.captcha-img'

  static readonly CAPTCHA_FORM = '#captchaForm'

  static readonly BTN_VERIFY = '#btnVerify'

  constructor(private readonly page: Page) {}

  /**
   * Ana captcha layout kapsayıcısı.
   */
  captchaContainer(): Locator {
    return this.page.locator(BlsCaptchaPage.CAPTCHA_MAIN_DIV)
  }

  /**
   * Tüm `captcha-img` karoları (grid).
   */
  captchaTiles(): Locator {
    return this.captchaContainer().locator(BlsCaptchaPage.CAPTCHA_TILE)
  }

  /**
   * Form çerçevesi — şifre bölümü için bağlam (`#captchaForm`).
   */
  captchaForm(): Locator {
    return this.page.locator(BlsCaptchaPage.CAPTCHA_FORM)
  }

  /** Doğrudan `#btnVerify` */
  legacySubmitButton(): Locator {
    return this.page.locator(BlsCaptchaPage.BTN_VERIFY)
  }

  /**
   * Doğrula: erişilebilir ad (`getByRole`) + `#btnVerify` + `input[type=submit]` (`blsVerifyLocators`).
   */
  verifySubmitSemantic(): Locator {
    return verifySubmitLocatorUnion(this.page)
  }

  /**
   * Captcha gövdesi + doğrula: zaman aşımında `captchaDataset.refresh()` ile puzzle yenile (en fazla 3).
   * `time.sleep` yok — yalnızca `expect`/poll.
   */
  async expectLogincaptchaShellVisibleWithDatasetRefresh(
    captchaDataset?: CaptchaDatasetCollector,
    timeoutMs: number = 60_000,
    maxCaptchaRefreshes: number = 3,
  ): Promise<void> {
    const deadline = Date.now() + timeoutMs
    let puzzleRefreshes = 0

    while (Date.now() < deadline) {
      const chunk = Math.min(
        12_000,
        Math.max(2_000, deadline - Date.now()),
      )
      try {
        await expect(this.captchaContainer()).toBeVisible({
          timeout: chunk,
        })
        await expect(this.verifySubmitSemantic().first()).toBeVisible({
          timeout: chunk,
        })
        return
      } catch {
        if (
          captchaDataset &&
          puzzleRefreshes < maxCaptchaRefreshes
        ) {
          const bounced = await captchaDataset.refresh(3).catch(() => false)
          if (bounced) puzzleRefreshes++
          continue
        }
        break
      }
    }

    await expect(this.captchaContainer()).toBeVisible({
      timeout: Math.min(8_000, Math.max(1_500, deadline - Date.now())),
    })
    await expect(this.verifySubmitSemantic().first()).toBeVisible({
      timeout: Math.min(8_000, Math.max(1_500, deadline - Date.now())),
    })
  }

  /**
   * Captcha alanı ve doğrula düğümü beklenir (web-first assertion; test içinden çağrılır).
   *
   * @param timeoutMs — Playwright beklemesi (ms)
   */
  async expectLogincaptchaShellVisible(timeoutMs: number = 60_000): Promise<void> {
    await expect(this.captchaContainer()).toBeVisible({ timeout: timeoutMs })
    await expect(this.verifySubmitSemantic().first()).toBeVisible({
      timeout: timeoutMs,
    })
  }

  /**
   * URL ipucu (navigasyon assert yardımcısı) — tam assert değildir.
   */
  static urlSuggestsLoginCaptcha(url: string): boolean {
    const u = url.toLowerCase()
    return u.includes('logincaptcha') || u.includes('newcaptcha')
  }

  /** OCR ham çıktısından yalnızca rakam dizisi (POM / test yardımı). */
  static sanitizeDigits(raw: string): string {
    return sanitizeCaptchaDigits(raw)
  }

  /** İki üç-hane (veya string) için Lev. mesafesi — `captcha-smart-match`. */
  static levenshtein(a: string, b: string): number {
    return levenshteinDistance(a, b)
  }

  /** Hedef üç hane ile OCR metni: `exact` | `fuzzy_high` (dist≤1) | `none`. */
  static evaluateOcrMatch(target: string, ocr: string) {
    return evaluateCaptchaOcrMatch(target, ocr)
  }

  /** Tıklama öncesi teyit — `[CAPTCHA]: Target … (Fuzzy|Exact: OK). Clicking tile...` */
  static logCaptchaClick(target: string, read: string, kind: 'exact' | 'fuzzy_high'): void {
    logCaptchaTileClick(target, read, kind)
  }

  /** @deprecated `logCaptchaClick` kullanın. */
  static logOcrSmart(target: string, sanitized: string, dist: number): void {
    logOcrSmartMatch(target, sanitized, dist)
  }

  /**
   * OCR öncesi — DPR ölçek, yüksek kontrast, keskinleştirme (`preprocessImage`).
   */
  static async preprocessImageTile(
    page: Page,
    base64WithoutPrefix: string,
    sourceMimeType: string,
  ): Promise<string> {
    return preprocessImage(page, base64WithoutPrefix, sourceMimeType)
  }

  /**
   * Geriye uyumluluk — `preprocessImageTile` ile aynı.
   *
   * @param sourceMimeType Örn. `image/gif`, `image/png`, `image/jpeg`
   */
  static async preprocessCaptchaTile(
    page: Page,
    base64WithoutPrefix: string,
    sourceMimeType: string,
  ): Promise<string> {
    return preprocessCaptcha(page, base64WithoutPrefix, sourceMimeType)
  }
}
