import { expect, type Locator, type Page } from '@playwright/test'

import { normalizeEmail } from '../../src/emailNormalize'

import { verifySubmitLocatorUnion } from './blsVerifyLocators'

/**
 * BLS adım 0 / login formu (`pages/login_page.BLSLoginPage` TS portunun POM katmanı).
 * E‑posta ve şifre için öncelikle rol / etiket tabanlı locator'lar kullanılır.
 */
export class BlsLoginPage {
  private static readonly EMAIL_LABEL_RE =
    /E-?posta|Email|E\s*posta|Username|Kullanici\s*adi/i

  private static readonly EMAIL_ROLE_RE =
    /E-?posta|mail|user\s*name|username|kullanici/i

  private static readonly PASSWORD_LABEL_RE =
    /Password|Şifre|Şifreniz|Parola|Parolanız|Enter\s*password/i

  private static readonly PASSWORD_HINT_RE =
    /password|şifre|parola|\*{2,}/i

  constructor(private readonly page: Page) {}

  /**
   * Doğrula / Verify / `input[type=submit]` — BLS’nin düğüm çeşitleri için birleşik zincir (`blsVerifyLocators`).
   */
  verifySubmitButtonSemantic(): Locator {
    return verifySubmitLocatorUnion(this.page)
  }

  /**
   * Şifre alanları: rol + `input[type=password]` filtresi veya doğrudan password input.
   */
  passwordFields(): Locator {
    return this.page
      .getByRole('textbox')
      .filter({ has: this.page.locator("input[type='password']") })
      .or(this.page.locator("input[type='password']"))
  }

  /**
   * Görünür e-posta girişleri — etiket veya metin kutusu adı ile.
   */
  async visibleEmailInputsOrdered(): Promise<Locator[]> {
    const out: Locator[] = []
    const byLabel = this.page.getByLabel(BlsLoginPage.EMAIL_LABEL_RE)
    for (const loc of await byLabel.all()) {
      if (!(await loc.isVisible())) continue
      const t = (await loc.getAttribute('type')) || ''
      if (t.toLowerCase() === 'password') continue
      out.push(loc)
    }
    if (out.length) return out

    const byRole = this.page.getByRole('textbox', {
      name: BlsLoginPage.EMAIL_ROLE_RE,
    })
    for (const loc of await byRole.all()) {
      if (await loc.isVisible()) out.push(loc)
    }
    return out
  }

  /**
   * İlk görünür e-posta kutusu (BLS obfuscate çoklu alan senaryosu).
   */
  async resolveFirstVisibleEmailLocator(): Promise<Locator | null> {
    const boxes = await this.visibleEmailInputsOrdered()
    return boxes[0] ?? null
  }

  /**
   * Görünür şifre kutuları sırası.
   */
  async visiblePasswordInputsOrdered(): Promise<Locator[]> {
    const vis: Locator[] = []
    for (const loc of await this.passwordFields().all()) {
      if (await loc.isVisible()) vis.push(loc)
    }
    return vis
  }

  /**
   * Randevu / ödeme paneli DOM ipuçları — `LoginStep._assert_dashboard_after_submit` ile uyumlu geniş regex.
   */
  appointmentDashboardIndicator(): Locator {
    return this.page.getByRole('heading', {
      name: /appointment|randevu|payment|odeme|ödeme/i,
    })
  }

  /**
   * Adım 0: doğrula denetimi DOM’da oluşsun ve görünür olsun (ek süre için `BLS_STEP0_VERIFY_TIMEOUT_MS`).
   */
  async waitForStep0LoginFormReady(timeoutMs: number = 60_000): Promise<void> {
    const envRaw = (process.env.BLS_STEP0_VERIFY_TIMEOUT_MS ?? '').trim()
    const envParsed = Number.parseInt(envRaw, 10)
    const t =
      Number.isFinite(envParsed) && envParsed > 5_000 ? envParsed : timeoutMs

    const btn = this.verifySubmitButtonSemantic().first()
    await expect(btn).toBeAttached({ timeout: Math.min(t, 35_000) })
    await expect(btn).toBeVisible({ timeout: t })
  }

  /**
   * BLS mobil: `type=email` bazen geçersiz; `text` + `inputmode=email` (Python `_apply_bls_email_input_mode`).
   */
  async applyBlsEmailInputMode(loc: Locator): Promise<void> {
    await loc.evaluate(
      /* istanbul ignore next */
      (el) => {
        if (!el || el.tagName !== 'INPUT') return
        const input = el as HTMLInputElement
        input.setAttribute('type', 'text')
        input.setAttribute('inputmode', 'email')
        input.setAttribute('autocomplete', 'email')
        input.setAttribute('autocapitalize', 'none')
        input.setAttribute('spellcheck', 'false')
      },
    )
  }

  /**
   * İlk e-posta kutusu: scroll + kutu içi rastgele tık + odak bekleme (`strict_prepare_first_email_focus`).
   */
  async strictPrepareFirstEmailFocus(): Promise<boolean> {
    const target = await this.resolveFirstVisibleEmailLocator()
    if (!target) return false
    try {
      await target.scrollIntoViewIfNeeded()
    } catch {
      /* devam */
    }
    const box = await target.boundingBox()
    if (!box || box.width <= 1 || box.height <= 1) {
      await target.click({ timeout: 15_000 })
    } else {
      const px = box.width * (0.15 + Math.random() * 0.7)
      const py = box.height * (0.15 + Math.random() * 0.7)
      await target.click({ position: { x: px, y: py }, timeout: 15_000 })
    }
    try {
      await expect(target).toBeFocused({ timeout: 2_000 })
    } catch {
      try {
        await expect(target).toBeFocused({ timeout: 900 })
      } catch {
        /* yumşak — fill yine çalışabilir */
      }
    }
    return true
  }

  /** Görünür e-posta kutularına doğrudan `fill` (`fill_visible_entry_disabled`). */
  async fillVisibleEntryDisabled(value: string): Promise<number> {
    const em = normalizeEmail(value)
    if (!em) return 0
    const boxes = await this.visibleEmailInputsOrdered()
    if (!boxes.length) return 0
    const vc = boxes.length
    if (vc === 1) {
      await this.applyBlsEmailInputMode(boxes[0])
      await boxes[0].fill(em)
      return 1
    }
    const limit = Math.min(em.length, vc)
    for (let idx = 0; idx < limit; idx++) {
      await this.applyBlsEmailInputMode(boxes[idx])
      await boxes[idx].fill(em[idx]!)
    }
    return limit
  }

  private pickTypingDelayMs(slow: boolean): number {
    return slow ? 90 + Math.floor(Math.random() * 55) : 45 + Math.floor(Math.random() * 40)
  }

  /** Karakter sıralı yazım — önce görünür odak için hafif tıklama. */
  async typeIntoLocatorHuman(
    loc: Locator,
    text: string,
    opts?: { slow?: boolean; email?: boolean },
  ): Promise<void> {
    const raw = opts?.email ? normalizeEmail(text) : text
    if (!raw) return
    try {
      await loc.scrollIntoViewIfNeeded()
    } catch {
      /* devam */
    }
    try {
      const bb = await loc.boundingBox()
      if (!bb || bb.width <= 1 || bb.height <= 1) {
        await loc.click({ timeout: 15_000 })
      } else {
        await loc.click({
          position: {
            x: bb.width * (0.2 + Math.random() * 0.6),
            y: bb.height * (0.2 + Math.random() * 0.6),
          },
          timeout: 15_000,
        })
      }
    } catch {
      await loc.click({ timeout: 15_000 }).catch(() => undefined)
    }
    if (opts?.email) await this.applyBlsEmailInputMode(loc)
    await loc.fill('')
    const delayMs = this.pickTypingDelayMs(opts?.slow ?? false)
    await loc.pressSequentially(raw, { delay: delayMs })
  }

  /**
   * E-postayı insan yazımı ile yazar (`type_primary_email_field_human`).
   * Odak hazırlığı çağıranın `strictPrepareFirstEmailFocus` çağrısı ile yapılır.
   */
  async typePrimaryEmailFieldHuman(
    value: string,
    opts?: { slow?: boolean },
  ): Promise<number> {
    const slow = opts?.slow ?? false
    const em = normalizeEmail(value)
    if (!em) return 0
    const boxes = await this.visibleEmailInputsOrdered()
    if (!boxes.length) return 0
    const vc = boxes.length
    if (vc === 1) {
      await this.typeIntoLocatorHuman(boxes[0], em, { slow, email: true })
      return 1
    }
    let filled = 0
    const limit = Math.min(em.length, vc)
    for (let idx = 0; idx < limit; idx++) {
      await this.typeIntoLocatorHuman(boxes[idx]!, em[idx]!, {
        slow,
        email: true,
      })
      filled++
    }
    return filled
  }

  /** `type_email_step0_human` ile aynı yüzey. */
  async typeEmailStep0Human(
    email: string,
    opts?: { slow?: boolean },
  ): Promise<number> {
    return this.typePrimaryEmailFieldHuman(email, opts)
  }

  /** Odaktan çıkmak için (0,0) tık — `_blur_after_password_fill`. */
  async blurFieldsByNeutralClick(): Promise<void> {
    try {
      await this.page.mouse.click(0, 0)
    } catch {
      /* yut */
    }
  }

  /** Görünür şifre alanı bekler (`await_visible_password_field` sade versiyonu). */
  async awaitVisiblePasswordField(timeoutMs: number = 30_000): Promise<Locator | null> {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      for (const loc of await this.visiblePasswordInputsOrdered()) {
        return loc
      }
      for (const loc of await this.page.getByRole('textbox').all()) {
        if (!(await loc.isVisible())) continue
        const cls = ((await loc.getAttribute('class')) || '').toLowerCase()
        if (cls.split(/\s+/).some((x) => x.includes('fakepassword'))) {
          return loc
        }
      }
      const alt = this.page
        .getByLabel(BlsLoginPage.PASSWORD_LABEL_RE)
        .or(this.page.getByRole('textbox', { name: BlsLoginPage.PASSWORD_HINT_RE }))
        .first()
      const hit = await alt.isVisible().catch(() => false)
      if (hit) return alt
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 120)
      })
    }
    return null
  }

  async typePasswordIfVisibleHuman(
    password: string,
    opts?: { slow?: boolean },
  ): Promise<number> {
    const pw = (password || '').trim()
    if (!pw) return 0
    const loc = await this.awaitVisiblePasswordField(30_000)
    if (!loc) return 0
    await this.typeIntoLocatorHuman(loc, pw, {
      slow: opts?.slow ?? false,
      email: false,
    })
    return 1
  }

  async typeSegmentedPasswordEntryDisabledHuman(
    password: string,
    opts?: { slow?: boolean },
  ): Promise<number> {
    const pw = password.trim()
    if (!pw) return 0
    const visible = await this.visiblePasswordInputsOrdered()
    if (!visible.length) return 0
    if (visible.length === 1) {
      await this.typeIntoLocatorHuman(visible[0], pw, {
        slow: opts?.slow ?? false,
        email: false,
      })
      return 1
    }
    const limit = Math.min(pw.length, visible.length)
    for (let idx = 0; idx < limit; idx++) {
      await this.typeIntoLocatorHuman(visible[idx], pw[idx]!, {
        slow: opts?.slow ?? false,
        email: false,
      })
    }
    return limit
  }

  /** `type_password_for_login_human` */
  async typePasswordForLoginHuman(
    password: string,
    opts?: { slow?: boolean },
  ): Promise<number> {
    const n = await this.typePasswordIfVisibleHuman(password, opts)
    if (n >= 1) return n
    return this.typeSegmentedPasswordEntryDisabledHuman(password, opts)
  }
}
