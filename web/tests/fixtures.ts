import { test as base, expect } from '@playwright/test'
import { createDefaultMockState, installBackendMock } from './backendMock'
import { API_ROUTE_PATTERN, useBackendRouteMock } from './mockConfig'
import {
  createCaptchaDatasetCollector,
  type CaptchaDatasetCollector,
} from './utils/captchaDataset'

type Fixtures = {
  /** Her test için ayrı in-memory backend durumu (yalnızca mock modunda kullanılır). */
  backendState: ReturnType<typeof createDefaultMockState>
  /** Route mock kurulumu (otomatik); testlerde kullanmayın. */
  _apiMockHooks: void
  /**
   * BLS CAPTCHA eğitim görüntüsü — `dataset/raw_captchas` (override: `BLS_CAPTCHA_DATASET_DIR`).
   * Ağ tabanlı otomatik kayıt için `BLS_CAPTCHA_DATASET_AUTOWATCH=1`.
   */
  captchaDataset: CaptchaDatasetCollector
}

export const test = base.extend<Fixtures>({
  /**
   * Her test icin ayri in-memory backend (yalnizca mock modunda).
   *
   * @param use - Playwright fixture lifecycle
   */
  backendState: async ({}, use) => {
    await use(createDefaultMockState())
  },

  /**
   * mockConfig.useBackendRouteMock() true ise API_ROUTE_PATTERN uzerinde mock kurar.
   *
   * @param page - Test sayfasi
   * @param backendState - Paylasilan mock durumu
   * @param use - Fixture lifecycle
   */
  _apiMockHooks: [
    async ({ page, backendState }, use) => {
      if (useBackendRouteMock()) await installBackendMock(page, backendState)
      await use(undefined as void)
      if (useBackendRouteMock()) await page.unroute(API_ROUTE_PATTERN)
    },
    { auto: true },
  ],

  /**
   * Captcha eğitim verisi: başarısız deneme (`captureOnFailedAttempt`) veya yeni puzzle
   * (`captureIfPuzzleChanged`) ile kullanıcı akışından çağrılır; isteğe bağlı olarak
   * captcha ağı isteklerinde debounce ile otomatik yakalar.
   */
  captchaDataset: async ({ page }, use) => {
    const collector = createCaptchaDatasetCollector(page, {
      outputDir:
        process.env.BLS_CAPTCHA_DATASET_DIR &&
        process.env.BLS_CAPTCHA_DATASET_DIR.trim()
          ? process.env.BLS_CAPTCHA_DATASET_DIR.trim()
          : undefined,
      containerSelector:
        process.env.BLS_CAPTCHA_CONTAINER &&
        process.env.BLS_CAPTCHA_CONTAINER.trim()
          ? process.env.BLS_CAPTCHA_CONTAINER.trim()
          : undefined,
      tileSelector:
        process.env.BLS_CAPTCHA_TILE_SELECTOR &&
        process.env.BLS_CAPTCHA_TILE_SELECTOR.trim()
          ? process.env.BLS_CAPTCHA_TILE_SELECTOR.trim()
          : undefined,
    })
    let unlisten: (() => void) | undefined
    const autowatch = ['1', 'true', 'yes', 'on'].includes(
      (process.env.BLS_CAPTCHA_DATASET_AUTOWATCH ?? '').trim().toLowerCase(),
    )
    if (autowatch) {
      unlisten = collector.startAutoCaptureOnCaptchaNetworkResponse()
    }
    await use(collector)
    unlisten?.()
    collector.dispose()
  },
})

export { expect }
export type { CaptchaDatasetCollector }
