/**
 * Gerçek BLS giriş URL’si gerektiren entegrasyon kabul testi (`BlsLoginFlow`).
 *
 * @remarks
 * - Ortam dolu değilse senaryolar atlanır.
 * - `127.0.0.1:8000` / `localhost:8000` istekleri `beforeEach` içinde stub ile yanıtlanır — backend kapalıyken gereksiz ECONNREFUSED gürültüsü azaltılır (BLS sekmesi dışı paralel talepler için).
 * - Başarısızlıkta: `dataset/failures/` PNG (`afterEach` + flow içi guard).
 */
import { expect, test } from './fixtures'
import { BlsLoginFlow } from './flows/BlsLoginFlow'
import { savePlaywrightFailureScreenshot } from './utils/failureScreenshot'

const loginUrl = (
  process.env.BLS_LOGIN_FLOW_TEST_URL ||
  process.env.BLS_LOGIN_URL ||
  ''
).trim()
const flowEmail = (process.env.BLS_LOGIN_FLOW_EMAIL || process.env.TEST_EMAIL || '').trim()
const flowPassword = (
  process.env.BLS_LOGIN_FLOW_PASSWORD ||
  process.env.TEST_PASSWORD ||
  ''
).trim()

function flowCredentials(passwordFallback: string) {
  return {
    loginUrl,
    email: flowEmail,
    password: passwordFallback,
  }
}

test.describe('BLS LoginFlow (env: BLS_LOGIN_URL | BLS_LOGIN_FLOW_TEST_URL)', () => {
  test.beforeEach(() => {
    test.skip(!loginUrl, 'BLS_LOGIN_URL veya BLS_LOGIN_FLOW_TEST_URL gerekli')
  })

  test.beforeEach(async ({ page }) => {
    const jsonOk = (): string =>
      JSON.stringify({ ok: true, stubbedByBlsSuite: true })

    await page.route(/http:\/\/127\.0\.0\.1:8000\/.*/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: jsonOk(),
      })
    })
    await page.route(/http:\/\/localhost:8000\/.*/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: jsonOk(),
      })
    })
  })

  test.afterEach(async ({ page }, testInfo) => {
    if (testInfo.status === 'failed' || testInfo.status === 'timedOut') {
      await savePlaywrightFailureScreenshot(page, testInfo)
    }
  })

  test('runStep0EmailOnly doldurur ve çıktı verir', async ({
    page,
    captchaDataset,
  }) => {
    if (!flowEmail) {
      test.skip()
      return
    }
    const flow = new BlsLoginFlow({
      page,
      credentials: flowCredentials(flowPassword || 'not-used-submit-false-later'),
      captchaDataset,
    })

    const outcome = await flow.runStep0EmailOnly()

    expect(outcome.filledEmailFields, 'En az bir e-posta slotu dolmalıdır').toBeGreaterThan(0)
    expect(outcome.filledPasswordFields).toBe(0)
    expect(outcome.reachedSessionHome).toBe(false)
  })

  test('run submitForm:false — şifre alanı yazılır', async ({
    page,
    captchaDataset,
  }) => {
    if (!flowEmail || !flowPassword.trim()) {
      test.skip()
      return
    }
    const flow = new BlsLoginFlow({
      page,
      credentials: flowCredentials(flowPassword),
      captchaDataset,
    })
    const outcome = await flow.run({ submitForm: false })
    expect(outcome.filledEmailFields).toBeGreaterThan(0)
    expect(outcome.reachedSessionHome).toBe(false)
  })

  test('run submitForm:true — eksik şifrede anlamlı hata', async ({
    page,
    captchaDataset,
  }) => {
    if (!flowEmail) {
      test.skip()
      return
    }
    const flow = new BlsLoginFlow({
      page,
      credentials: {
        loginUrl,
        email: flowEmail,
        password: '',
      },
      captchaDataset,
    })
    await expect(flow.run({ submitForm: true })).rejects.toThrow(
      /Şifre eksik|password/i,
    )
  })
})
