import type { Page } from '@playwright/test'
import { expect } from '@playwright/test'

import { CaptchaOcrService } from '../../src/utils/captcha-ocr-solver'
import type { CaptchaDatasetCollector } from '../utils/captchaDataset'
import {
  defaultStep0CaptchaLocator,
  stabilizeAfterCaptchaTrigger,
  stabilizeAfterLoginFormReady,
  stabilizeNavigationThenLoginAnchors,
  waitOptionalVisible,
} from '../utils/blsWebFirstStabilize'
import { saveLoginFlowFailureScreenshot } from '../utils/failureScreenshot'
import { BlsCaptchaPage } from '../pages/BlsCaptchaPage'
import { BlsLoginPage } from '../pages/BlsLoginPage'

/** Panel / ortamdan gelen giriş parametreleri (`LoginCredentials` TS karşılığı). */
export interface BlsLoginCredentials {
  loginUrl: string
  email: string
  password: string
}

/** `steps/login_step.LoginStepOutcome` ile hizalı sonuç modeli. */
export interface LoginStepOutcome {
  filledEmailFields: number
  filledPasswordFields: number
  reachedSessionHome: boolean
}

/** Submit sonrası makine durumu (`login_step.PostSubmitKind`). */
export type PostSubmitKind =
  | 'error'
  | 'challenge'
  | 'password_round'
  | 'session_home'
  | 'ambiguous'
  | 'cookie_retry'

export interface IBlsLoginFlow {
  readonly credentials: BlsLoginCredentials
  run(opts?: BlsLoginRunOptions): Promise<LoginStepOutcome>
  runStep0EmailOnly(opts?: BlsLoginRunOptions): Promise<LoginStepOutcome>
}

export interface BlsLoginRunOptions {
  /** `false`: Python `submit_form=False`; e‑posta sonrası şifre yaz ve dur (Doğrula yok). */
  submitForm?: boolean
}

export interface BlsLoginFlowDeps {
  page: Page
  credentials: BlsLoginCredentials
  profileId?: number
  apiBase?: string | undefined
  captchaDataset?: CaptchaDatasetCollector | undefined
}

const GOTO_NET_ERR_MARKERS = [
  'ERR_CONNECTION_TIMED_OUT',
  'ERR_CONNECTION_REFUSED',
  'ERR_CONNECTION_RESET',
  'ERR_NAME_NOT_RESOLVED',
  'ERR_INTERNET_DISCONNECTED',
  'ERR_NETWORK_CHANGED',
  'net::ERR_',
] as const

function isLikelyNavigationNetworkError(message: string): boolean {
  return GOTO_NET_ERR_MARKERS.some((m) => message.includes(m))
}

function readIntEnv(name: string, defaultValue: number): number {
  const raw = (process.env[name] ?? '').trim()
  if (!raw) return defaultValue
  const n = Number.parseInt(raw, 10)
  return Number.isFinite(n) ? n : defaultValue
}

/**
 * BLS giriş akışının TypeScript karşılığı (`steps/login_step.LoginStep` ile kademeli parity).
 *
 * Bu sürüm: `goto` + form beklemesi + e‑posta yazımı + Enter sonrası captcha görünürlüğünde
 * OCR (Levenshtein≤1 fuzzy + yenileme, `maxRetries` env) ile karo seçimi + dataset kaydı.
 */
export class BlsLoginFlow implements IBlsLoginFlow {
  readonly loginPage: BlsLoginPage

  readonly captchaPage: BlsCaptchaPage

  constructor(readonly deps: BlsLoginFlowDeps) {
    this.loginPage = new BlsLoginPage(deps.page)
    this.captchaPage = new BlsCaptchaPage(deps.page)
  }

  get credentials(): BlsLoginCredentials {
    return this.deps.credentials
  }

  /** Başarısız adımda veya beklenmedik hata: negatif örnek için görüntü toplama. */
  protected async notifyCaptchaAttemptFailed(context: string): Promise<void> {
    const collector = this.deps.captchaDataset
    if (!collector) return
    await collector.captureOnFailedAttempt({ suiteTag: 'login_flow', note: context })
  }

  /** Yeni puzzle / görsel parmak izi değişimi (Enter veya yenile sonrası). */
  protected async notifyPossibleNewCaptcha(context: string): Promise<void> {
    const collector = this.deps.captchaDataset
    if (!collector) return
    await collector.captureIfPuzzleChanged('new_puzzle', {
      suiteTag: 'login_flow',
      note: context,
    })
  }

  /** Hata yakala → dataset yaz → yeniden yükselt. */
  private async guardStageFailure<T>(
    stage: string,
    action: () => Promise<T>,
  ): Promise<T> {
    try {
      return await action()
    } catch (err: unknown) {
      await saveLoginFlowFailureScreenshot(this.deps.page, stage)
      await this.notifyCaptchaAttemptFailed(`stage:${stage}:${String(err)}`.slice(0, 280))
      throw err
    }
  }

  private getGotoTimeoutMs(): number {
    return readIntEnv('BLS_GOTO_TIMEOUT_MS', 60_000)
  }

  private getGotoRetryMax(): number {
    return readIntEnv('BLS_GOTO_RETRY_MAX', 2)
  }

  /** `LoginStep.run` / `run_step0_email_only` ile aynı URL gitme politikası (`commit` son deneme). */
  async gotoLoginUrl(): Promise<void> {
    const { page } = this.deps
    const url = this.deps.credentials.loginUrl.trim()
    if (!url) {
      throw new Error('BlsLoginCredentials.loginUrl boş olamaz.')
    }
    const timeoutMs = this.getGotoTimeoutMs()
    const retryMax = this.getGotoRetryMax()

    await this.guardStageFailure('goto', async () => {
      let lastErr: unknown
      for (let attempt = 0; attempt <= retryMax; attempt++) {
        const waitUntil =
          attempt === retryMax ? ('commit' as const) : ('domcontentloaded' as const)
        try {
          await page.goto(url, { waitUntil, timeout: timeoutMs })
          return
        } catch (e: unknown) {
          lastErr = e
          const msg = String(e ?? '')
          if (
            attempt < retryMax &&
            isLikelyNavigationNetworkError(msg)
          ) {
            await new Promise<void>((resolve) => {
              setTimeout(resolve, 4_000 * (attempt + 1))
            })
            continue
          }
          throw e
        }
      }
      throw lastErr
    })
  }

  /**
   * E‑posta yazılmadan önce captcha kapsayıcısı veya ağ / `domcontentloaded` (`_stabilize_before_email_fill`).
   */
  async stabilizeBeforeEmailFill(): Promise<void> {
    const cap = defaultStep0CaptchaLocator(this.deps.page)
    const overall = Math.max(readIntEnv('BLS_EMAIL_PRE_STABILIZE_MS', 9_000), 9_000)
    await stabilizeAfterLoginFormReady(this.deps.page, cap, overall)
  }

  /** `goto` sonrası doğrula DOM ankrajı — `waitForStep0` öncesi çağrılmalı. */
  async stabilizeAfterNavigation(): Promise<void> {
    const cap = defaultStep0CaptchaLocator(this.deps.page)
    await stabilizeNavigationThenLoginAnchors(this.deps.page, cap)
  }

  /** E‑posta bastıktan sonra Enter + tetik stabilizasyonu (`_trigger_email_enter_and_stabilize`). */
  async triggerEmailEnterAndStabilize(): Promise<void> {
    const { page } = this.deps
    await page.keyboard.press('Enter')
    const cap = defaultStep0CaptchaLocator(page)
    await stabilizeAfterCaptchaTrigger(page, cap, 5_000)
  }

  /** Sunucu validation tetikleri için (`_blur_after_password_fill`). */
  async blurAfterFields(): Promise<void> {
    await this.loginPage.blurFieldsByNeutralClick()
  }

  /**
   * Tek satırlık birleşik e‑posta yazımı (`_fill_email_unified` — OCR modunda insan yazımı).
   *
   * @param slow daha uzun `press_sequentially` gecikmesi
   * @returns doldurulan slot sayısı
   */
  async fillEmailUnified(slow: boolean = false): Promise<number> {
    const form = this.loginPage
    await form.strictPrepareFirstEmailFocus()

    let nPrimary = await form.typePrimaryEmailFieldHuman(this.credentials.email, { slow })
    if (nPrimary < 1) {
      nPrimary = await form.typeEmailStep0Human(this.credentials.email, { slow })
    }
    if (nPrimary < 1) {
      nPrimary = await form.fillVisibleEntryDisabled(this.credentials.email)
    }
    if (nPrimary >= 1) {
      await this.triggerEmailEnterAndStabilize()
      await this.blurAfterFields()
      const btn = form.verifySubmitButtonSemantic().first()
      if (
        !(await waitOptionalVisible(btn, 1_500))
      ) {
        try {
          await expect(btn).toBeVisible({ timeout: 400 })
        } catch {
          /* yumşak */
        }
      }
    }
    return nPrimary
  }

  /**
   * Captcha grid’i görünürse dataset’e parmak izi değiştiyse kaydet (eğitim etiketleme için).
   */
  async recordCaptchaPhaseIfPresent(context: string): Promise<void> {
    const page = this.deps.page
    const container =
      (process.env.BLS_CAPTCHA_CONTAINER ?? '').trim() || '.captcha-wrapper'
    const capLocator = page
      .locator(container)
      .filter({ has: page.getByRole('img') })
      .first()
    try {
      await expect(capLocator).toBeVisible({ timeout: 3_500 })
      await this.notifyPossibleNewCaptcha(context)
    } catch {
      const ds = this.deps.captchaDataset
      if (ds) {
        const bounced = await ds.refresh(3).catch(() => false)
        if (bounced) {
          try {
            await expect(capLocator).toBeVisible({ timeout: 8_000 })
            await this.notifyPossibleNewCaptcha(`${context}|post_dataset_refresh`)
            return
          } catch {
            /* fall through */
          }
        }
      }
      await this.recordLoginCaptchaAlternative(context)
    }
  }

  /** `#captcha-main-div` yolu (bazı oturumlarda `.captcha-wrapper` boş kalabilir). */
  private async recordLoginCaptchaAlternative(context: string): Promise<void> {
    const fallback = this.deps.page.locator('#captcha-main-div, form#captchaForm')
    try {
      await expect(fallback.first()).toBeVisible({ timeout: 2_000 })
      await this.notifyPossibleNewCaptcha(`${context}|fallback_selector`)
    } catch {
      /* captcha fazı değil */
    }
  }

  /** `steps/login_step.run_step0_email_only` parity. */
  async runStep0EmailOnly(opts?: BlsLoginRunOptions): Promise<LoginStepOutcome> {
    void opts
    await this.gotoLoginUrl()

    await this.guardStageFailure('post_goto_shell', async () => {
      await this.stabilizeAfterNavigation()
    })

    await this.guardStageFailure('wait_form_ready', async () => {
      await this.loginPage.waitForStep0LoginFormReady()
    })

    await this.stabilizeBeforeEmailFill()

    const nEmail = await this.guardStageFailure(
      'fill_email',
      async () => this.fillEmailUnified(false),
    )

    if (nEmail < 1) {
      await this.notifyCaptchaAttemptFailed('no_visible_email_slots')
      await saveLoginFlowFailureScreenshot(this.deps.page, 'no_visible_email_slots')
      throw new Error(
        'Adım 0: görünür e-posta kutusu bulunamadı (entry-disabled/bölünmüş yapı uyumsuz?).',
      )
    }

    await this.recordCaptchaPhaseIfPresent('run_step0_email_only_after_enter')

    return {
      filledEmailFields: nEmail,
      filledPasswordFields: 0,
      reachedSessionHome: false,
    }
  }

  /**
   * `LoginStep.run` başlangıç fazı (`submit_form` dallanması dahil — tam CAPTCHA doğrulama sonra).
   */
  async run(opts?: BlsLoginRunOptions): Promise<LoginStepOutcome> {
    const submitForm = opts?.submitForm !== false

    await this.gotoLoginUrl()

    await this.guardStageFailure('post_goto_shell', async () => {
      await this.stabilizeAfterNavigation()
    })

    await this.guardStageFailure('wait_form_ready', async () => {
      await this.loginPage.waitForStep0LoginFormReady()
    })

    await this.stabilizeBeforeEmailFill()

    const nEmail = await this.guardStageFailure(
      'fill_email',
      async () => this.fillEmailUnified(false),
    )

    if (nEmail < 1) {
      await this.notifyCaptchaAttemptFailed('no_visible_email_slots')
      await saveLoginFlowFailureScreenshot(this.deps.page, 'no_visible_email_slots')
      throw new Error('Görünür e-posta alanı yok (selector veya sayfa durumu).')
    }

    const capPollMs = readIntEnv('BLS_AFTER_EMAIL_CAPTCHA_POLL_MS', 22_000)
    await stabilizeAfterCaptchaTrigger(
      this.deps.page,
      defaultStep0CaptchaLocator(this.deps.page),
      Math.max(9_000, capPollMs),
    )

    await this.recordCaptchaPhaseIfPresent('run_after_email_stabilize')

    await this.guardStageFailure('captcha_ocr_smart', async () => {
      const captchaSolver = new CaptchaOcrService({
        maxRetries: readIntEnv('BLS_CAPTCHA_TS_MAX_REFRESH', 3),
      })
      if (!(await captchaSolver.isCaptchaVisible(this.deps.page))) return
      await this.captchaPage.expectLogincaptchaShellVisibleWithDatasetRefresh(
        this.deps.captchaDataset,
      )
      const sr = await captchaSolver.solveCaptcha(this.deps.page)
      if (!sr.success) {
        await this.notifyCaptchaAttemptFailed(
          `captcha_ocr_fail:retries=${sr.retryCount}|target=${sr.targetNumber}`,
        )
      }
    })

    if (!submitForm) {
      const nPwd = await this.guardStageFailure(
        'type_password_human_no_submit',
        async () =>
          this.loginPage.typePasswordForLoginHuman(this.credentials.password, {
            slow: false,
          }),
      )
      return {
        filledEmailFields: nEmail,
        filledPasswordFields: nPwd,
        reachedSessionHome: false,
      }
    }

    const pwdReq = !!(this.credentials.password || '').trim()
    if (!pwdReq) {
      await this.notifyCaptchaAttemptFailed('password_missing_submit_true')
      throw new Error(
        'Şifre eksik — tam giriş (submitForm: true) için panelden güncellenmelidir.',
      )
    }

    return {
      filledEmailFields: nEmail,
      filledPasswordFields: 0,
      reachedSessionHome: false,
      // Sonraki iterasyon: CAPTCHA-FIRST sonrası şifre doldurma, Doğrula, dashboard assert
    }
  }
}
