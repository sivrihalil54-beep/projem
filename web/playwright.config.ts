import { defineConfig, devices } from '@playwright/test'

/**
 * Offline (varsayilan): `tests/mockConfig.ts` + `fixtures` ile `/api/**` istekleri
 * mock'lanır (ECONNREFUSED olmaz). Gercek API: USE_REAL_API=1 (veya true, yes, on)
 * ile `tests/fixtures.ts` mock'u takmaz — Vite proxy 127.0.0.1:8000 bekler.
 *
 * - Headless: yerel gelistirmede `false` (CI veya PW_HEADLESS=1 ile `true`).
 * - Proxy: PLAYWRIGHT_PROXY (ornek http://host:8080); istege bagli PLAYWRIGHT_PROXY_USERNAME / PLAYWRIGHT_PROXY_PASSWORD.
 *
 * Ayrinti: tests/mockConfig.ts (API_ROUTE_PATTERN, useBackendRouteMock).
 */
const CHROME_UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

const headless =
  (!!process.env.CI && process.env.CI !== 'false') ||
  ['1', 'true', 'yes', 'on'].includes((process.env.PW_HEADLESS ?? '').trim().toLowerCase())

function proxyFromEnv(): { server: string; username?: string; password?: string } | undefined {
  const server = (process.env.PLAYWRIGHT_PROXY ?? '').trim()
  if (!server) return undefined
  const proxy: { server: string; username?: string; password?: string } = { server }
  const u = (process.env.PLAYWRIGHT_PROXY_USERNAME ?? '').trim()
  const p = (process.env.PLAYWRIGHT_PROXY_PASSWORD ?? '').trim()
  if (u) proxy.username = u
  if (p) proxy.password = p
  return proxy
}

const proxy = proxyFromEnv()

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [['list']],
  /**
   * OCR testleri (captcha-ocr.spec.ts): Tesseract her görüntü için ~2-5s harcar.
   * 10 karo × 4 PSM modu = ~40+ saniye; timeout buna göre ayarlandı.
   * Harici çözüm servisi kullanılmaz.
   */
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: {
    ...devices['Desktop Chrome'],
    headless,
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    extraHTTPHeaders: {
      'User-Agent': CHROME_UA,
    },
    baseURL:
      process.env.PLAYWRIGHT_BASE_URL ??
      process.env.VITE_PREVIEW_URL ??
      'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    ...(proxy ? { proxy } : {}),
  },
  projects: [{ name: 'chromium', use: { channel: undefined } }],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    cwd: '.',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
