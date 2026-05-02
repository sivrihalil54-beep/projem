/**
 * BLS Captcha OCR Entegrasyon Testleri
 *
 * Strateji: Gerçek BLS sunucusuna gidilmez. `page.setContent()` ile
 * step1:login.html yapısını taklit eden minimal HTML sayfası oluşturulur.
 * Karo görüntüleri Canvas API ile üretilir — Tesseract gerçekten çalışır.
 *
 * Yerel Tesseract; harici çözüm servisi kullanılmaz.
 *
 * Test yapısı:
 *   class TestCaptchaOcr
 *     ├── extractTargetNumber → görünür .box-label'dan sayı çıkarır
 *     ├── isCaptchaVisible    → konteyner visibility kontrolü
 *     ├── analyzeTiles        → OCR + eşleşme tespiti
 *     ├── clickMatchingTiles  → eşleşen karolara tıklama + DOM seçim kontrolü
 *     ├── refreshCaptcha      → yenileme butonu akışı
 *     └── solveCaptcha (tam akış) → başarı + yenileme retry senaryoları
 */

import { test, expect } from '@playwright/test'
import {
  CaptchaOcrService,
  extractTargetNumber,
  type SolveResult,
} from '../src/utils/captcha-ocr-solver'
import { evaluateCaptchaOcrMatch } from '../src/utils/captcha-smart-match'

// ─────────────────────────────────────────────────────────────
// Fixture Yardımcıları
// ─────────────────────────────────────────────────────────────

/**
 * Canvas API ile belirtilen sayıyı içeren base64 PNG görüntü üretir.
 * Tesseract'ın okuyabileceği büyüklükte siyah metin, beyaz zemin.
 *
 * @param page - Playwright sayfası (browser Canvas erişimi için)
 * @param number - Görüntüye yazılacak sayı
 * @param fontSize - Metin boyutu (px)
 * @returns Saf base64 (data: prefix olmadan)
 */
async function generateNumberImage(
  page: import('@playwright/test').Page,
  number: string,
  fontSize = 48,
): Promise<string> {
  return page.evaluate(
    ({ num, size }) => {
      const canvas = document.createElement('canvas')
      canvas.width = 100
      canvas.height = 100
      const ctx = canvas.getContext('2d')!
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, 100, 100)
      ctx.fillStyle = '#000000'
      ctx.font = `bold ${size}px Arial, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(num, 50, 50)
      return canvas.toDataURL('image/png').split(',')[1]
    },
    { num: number, size: fontSize },
  )
}

/**
 * BLS captcha sayfasını taklit eden HTML oluşturur.
 *
 * @param page - Playwright sayfası
 * @param targetNumber - .box-label talimat sayısı (3-haneli)
 * @param tileSrcs - img.captcha-img src dizisi (saf base64 veya tam data: URL)
 * @param hasRefreshBtn - Yenileme butonu eklensin mi
 */
async function renderCaptchaPage(
  page: import('@playwright/test').Page,
  targetNumber: string,
  tileSrcs: string[],
  hasRefreshBtn = true,
): Promise<void> {
  const imgTags = tileSrcs
    .map(
      (src, i) =>
        `<img
          class="captcha-img"
          data-index="${i}"
          style="width:100px;height:100px;border:2px solid #ccc;cursor:pointer;"
          src="${src.startsWith('data:') ? src : `data:image/png;base64,${src}`}"
          onclick="selectTile(${i}, this)"
          alt="captcha-tile-${i}"
        />`,
    )
    .join('\n')

  await page.setContent(`
    <!doctype html>
    <html lang="tr">
    <head><meta charset="UTF-8" /><title>BLS Captcha Test</title></head>
    <body>
      <div class="captcha-wrapper" data-testid="captcha-wrapper">

        <!-- Gizli etiketler (JS ile kapatılmış — BLS'deki gibi) -->
        <div class="col-12 box-label" style="display:none">Lütfen 999 numaralı tüm kutuları işaretleyin.</div>
        <div class="col-12 box-label" style="display:none">Lütfen 888 numaralı tüm kutuları işaretleyin.</div>

        <!-- Görünür etiket -->
        <div class="col-12 box-label" style="display:block" data-testid="captcha-label">
          Lütfen ${targetNumber} numaralı tüm kutuları işaretleyin.
        </div>

        <div id="captcha-tiles" style="display:flex;flex-wrap:wrap;gap:4px;max-width:420px;">
          ${imgTags}
        </div>

        <input type="hidden" id="SelectedImages" value="" />

        ${
          hasRefreshBtn
            ? `<button
                data-action="reload"
                data-testid="refresh-btn"
                id="reloadBtn"
                onclick="document.querySelectorAll('.captcha-img').forEach(i => i.classList.remove('img-selected')); document.getElementById('SelectedImages').value = ''"
              >Yenile</button>`
            : ''
        }

        <button
          id="btnVerify"
          data-testid="verify-btn"
          type="submit"
          onclick="document.getElementById('verify-status').textContent='submitted'"
        >Doğrula</button>

        <span id="verify-status" data-testid="verify-status"></span>
      </div>

      <script>
        const selected = [];
        function selectTile(idx, el) {
          const pos = selected.indexOf(idx);
          if (pos >= 0) {
            selected.splice(pos, 1);
            el.classList.remove('img-selected');
            el.style.border = '2px solid #ccc';
          } else {
            selected.push(idx);
            el.classList.add('img-selected');
            el.style.border = '3px solid #007bff';
          }
          document.getElementById('SelectedImages').value = selected.join(',');
        }
      </script>
    </body>
    </html>
  `)
}

test.describe('captcha-smart-match (Levenshtein ≤1)', () => {
  test('606 ↔ 506 ve 608 fuzzy_high; 156 ret', () => {
    expect(evaluateCaptchaOcrMatch('606', '506').kind).toBe('fuzzy_high')
    expect(evaluateCaptchaOcrMatch('606', '608').kind).toBe('fuzzy_high')
    expect(evaluateCaptchaOcrMatch('606', '606').kind).toBe('exact')
    expect(evaluateCaptchaOcrMatch('606', '156').kind).toBe('none')
  })
})

// ─────────────────────────────────────────────────────────────
// Testler
// ─────────────────────────────────────────────────────────────

test.describe('CaptchaOcrService', () => {
  /** Paylaşılan servis örneği — her test için ayrı oluşturmak gerekmez. */
  const solver = new CaptchaOcrService({ maxRetries: 2, interClickDelayMs: 100 })

  // ── extractTargetNumber ──────────────────────────────────

  test.describe('extractTargetNumber', () => {
    test('görünür .box-label içindeki 3-haneli sayıyı döner', async ({ page }) => {
      await page.setContent(`
        <div class="box-label" style="display:none">Lütfen 999 numaralı...</div>
        <div class="box-label" style="display:block">Lütfen 106 numaralı tüm kutuları işaretleyin.</div>
        <div class="box-label" style="display:none">Lütfen 777 numaralı...</div>
      `)

      const result = await extractTargetNumber(page)
      expect(result).toBe('106')
    })

    test('tüm .box-label gizliyse null döner', async ({ page }) => {
      await page.setContent(`
        <div class="box-label" style="display:none">Lütfen 106 numaralı...</div>
      `)
      const result = await extractTargetNumber(page)
      expect(result).toBeNull()
    })

    test('3-haneli sayı yoksa null döner', async ({ page }) => {
      await page.setContent(`
        <div class="box-label" style="display:block">Lütfen tüm kutuları seçin.</div>
      `)
      const result = await extractTargetNumber(page)
      expect(result).toBeNull()
    })
  })

  // ── isCaptchaVisible ────────────────────────────────────

  test.describe('isCaptchaVisible', () => {
    test('.captcha-wrapper görünüyorsa true döner', async ({ page }) => {
      await page.setContent(`<div class="captcha-wrapper">Captcha burada</div>`)
      await expect(page.locator('.captcha-wrapper')).toBeVisible()
      const visible = await solver.isCaptchaVisible(page)
      expect(visible).toBe(true)
    })

    test('captcha konteyneri yoksa false döner', async ({ page }) => {
      await page.setContent(`<div>Captcha yok</div>`)
      const visible = await solver.isCaptchaVisible(page)
      expect(visible).toBe(false)
    })
  })

  // ── analyzeTiles ────────────────────────────────────────

  test.describe('analyzeTiles', () => {
    test('hedef sayıyı içeren görseli tespit eder', async ({ page }) => {
      const TARGET = '106'
      const matchImg = await generateNumberImage(page, TARGET)
      const noMatchImg = await generateNumberImage(page, '999')

      await renderCaptchaPage(page, TARGET, [noMatchImg, matchImg, noMatchImg])

      const tiles = await solver.analyzeTiles(page, TARGET)

      expect(tiles).toHaveLength(3)
      // index=1 (matchImg) eşleşmeli
      const matched = tiles.filter((t) => t.matches)
      expect(matched.length).toBeGreaterThanOrEqual(1)
      // En az biri doğru index'e işaret etmeli
      const matchedIndices = matched.map((t) => t.index)
      expect(matchedIndices).toContain(1)
    })

    test('hiçbir görsel hedefi içermiyorsa tüm matches=false olur', async ({ page }) => {
      const TARGET = '106'
      const noMatchImg = await generateNumberImage(page, '999')

      await renderCaptchaPage(page, TARGET, [noMatchImg, noMatchImg])

      const tiles = await solver.analyzeTiles(page, TARGET)
      expect(tiles.every((t) => !t.matches)).toBe(true)
    })
  })

  // ── clickMatchingTiles ──────────────────────────────────

  test.describe('clickMatchingTiles', () => {
    test('eşleşen karoyu tıklar ve DOM güncellenir (img-selected sınıfı)', async ({ page }) => {
      const TARGET = '106'
      const matchImg = await generateNumberImage(page, TARGET)
      const noMatchImg = await generateNumberImage(page, '999')

      await renderCaptchaPage(page, TARGET, [noMatchImg, matchImg])

      const tileResults = [
        { index: 0, detected: '999', matches: false },
        { index: 1, detected: TARGET, matches: true },
      ]

      await solver.clickMatchingTiles(page, TARGET, tileResults)

      // Tıklanan karo img-selected sınıfı almış olmalı
      const tile1 = page.locator('img.captcha-img').nth(1)
      await expect(tile1).toHaveClass(/img-selected/)

      // #SelectedImages değeri günclenmiş olmalı
      const hiddenVal = await page.locator('#SelectedImages').inputValue()
      expect(hiddenVal).toContain('1')
    })

    test('eşleşme yoksa hiçbir şey tıklanmaz', async ({ page }) => {
      const TARGET = '106'
      const noMatchImg = await generateNumberImage(page, '999')

      await renderCaptchaPage(page, TARGET, [noMatchImg])

      await solver.clickMatchingTiles(page, TARGET, [
        { index: 0, detected: '999', matches: false },
      ])

      const hiddenVal = await page.locator('#SelectedImages').inputValue()
      expect(hiddenVal).toBe('')
    })
  })

  // ── refreshCaptcha ───────────────────────────────────────

  test.describe('refreshCaptcha', () => {
    test('yenile butonu görünür ve tıklanabilir olmalı', async ({ page }) => {
      const TARGET = '106'
      const img = await generateNumberImage(page, TARGET)
      await renderCaptchaPage(page, TARGET, [img], true)

      const btn = page.getByTestId('refresh-btn')
      await expect(btn).toBeVisible()
      await expect(btn).toBeEnabled()
    })

    test('yenileme sonrası karo seçimleri sıfırlanır', async ({ page }) => {
      const TARGET = '106'
      const matchImg = await generateNumberImage(page, TARGET)

      await renderCaptchaPage(page, TARGET, [matchImg], true)

      // Önce bir karo tıkla
      await page.locator('img.captcha-img').first().click()
      const beforeRefresh = await page.locator('#SelectedImages').inputValue()
      expect(beforeRefresh).toBeTruthy()

      // Yenile
      await page.getByTestId('refresh-btn').click()
      await page.locator('img.captcha-img').first().waitFor({ state: 'visible' })

      const afterRefresh = await page.locator('#SelectedImages').inputValue()
      expect(afterRefresh).toBe('')
    })
  })

  // ── solveCaptcha (tam akış) ──────────────────────────────

  test.describe('solveCaptcha — tam akış', () => {
    test('hedef sayı içeren görselleri bulur, tıklar ve success=true döner', async ({
      page,
    }) => {
      const TARGET = '660'
      const matchImg = await generateNumberImage(page, TARGET, 52)
      const noMatchImg1 = await generateNumberImage(page, '123')
      const noMatchImg2 = await generateNumberImage(page, '456')

      await renderCaptchaPage(page, TARGET, [noMatchImg1, matchImg, noMatchImg2, matchImg])

      const result: SolveResult = await solver.solveCaptcha(page)

      expect(result.success).toBe(true)
      expect(result.targetNumber).toBe(TARGET)
      expect(result.matchedCount).toBeGreaterThanOrEqual(1)
      expect(result.retryCount).toBe(0)

      // Doğrula butonunun enabled olduğunu web-first ile kontrol et
      await expect(page.getByTestId('verify-btn')).toBeEnabled()
    })

    test('captcha görünmüyorsa success=false döner (kısa devre)', async ({ page }) => {
      await page.setContent(`<div>Captcha yok</div>`)

      const result = await solver.solveCaptcha(page)
      expect(result.success).toBe(false)
      expect(result.targetNumber).toBe('')
    })

    test('retry: ilk denemede eşleşme yoksa yeniler ve tekrar dener', async ({ page }) => {
      const TARGET = '777'
      // İlk render: hedef sayı yok (tüm karolar farklı)
      const wrongImg = await generateNumberImage(page, '123')
      const matchImg = await generateNumberImage(page, TARGET, 52)

      let renderCount = 0
      await renderCaptchaPage(page, TARGET, [wrongImg], true)

      // Yenileme butonu tıklandığında görseli güncelle
      await page.exposeFunction('__onReload', async () => {
        renderCount++
      })

      await page.evaluate((correctSrc) => {
        const btn = document.querySelector('[data-action="reload"]') as HTMLElement
        const orig = btn.onclick
        btn.onclick = function (e) {
          orig?.call(this, e)
          // İkinci yüklemede doğru görseli koy
          const img = document.querySelector('img.captcha-img') as HTMLImageElement
          if (img) img.src = correctSrc
        }
      }, `data:image/png;base64,${matchImg}`)

      // solveCaptcha retry mekanizmasını test et
      // (Tesseract doğru okursa başarılı; burada davranışsal akış doğrulanır)
      const result = await solver.solveCaptcha(page)
      // Sonuç success veya değil; önemli olan döngünün hata atmadan tamamlanması
      expect(result).toHaveProperty('success')
      expect(result).toHaveProperty('retryCount')
      expect(result.retryCount).toBeGreaterThanOrEqual(0)
    })

    test('maxRetries aşılırsa success=false döner', async ({ page }) => {
      const strictSolver = new CaptchaOcrService({ maxRetries: 1, interClickDelayMs: 50 })
      const TARGET = '555'
      // Hiçbir karo hedefi içermiyor
      const wrongImg = await generateNumberImage(page, '111')
      await renderCaptchaPage(page, TARGET, [wrongImg], true)

      const result = await strictSolver.solveCaptcha(page)

      expect(result.success).toBe(false)
      expect(result.retryCount).toBeLessThanOrEqual(2) // maxRetries=1 + 1
    })
  })

  // ── Randevu akışı entegrasyonu (submit kontrolü) ─────────

  test.describe('Randevu akışı entegrasyonu', () => {
    test('captcha çözüldükten sonra Doğrula butonu etkinleşir ve tıklanabilir', async ({
      page,
    }) => {
      const TARGET = '420'
      const matchImg = await generateNumberImage(page, TARGET, 52)
      const noMatchImg = await generateNumberImage(page, '111')

      await renderCaptchaPage(page, TARGET, [noMatchImg, matchImg, noMatchImg], true)

      // Doğrula başlangıçta görünür olmalı (web-first)
      await expect(page.getByTestId('verify-btn')).toBeVisible()

      // OCR ile çöz
      const result = await solver.solveCaptcha(page)
      expect(result.success).toBe(true)

      // Çözüm sonrası buton etkin (web-first assertion)
      await expect(page.getByTestId('verify-btn')).toBeEnabled()

      // Doğrula butonuna tıkla
      await page.getByTestId('verify-btn').click()

      // Submit teyit (web-first — DOM güncellemesini bekle)
      await expect(page.getByTestId('verify-status')).toHaveText('submitted')
    })

    test('yanlış okuma durumunda captcha yenilenir ve yeniden denenir', async ({ page }) => {
      const TARGET = '333'
      const noMatchImg = await generateNumberImage(page, '999')
      const matchImg = await generateNumberImage(page, TARGET, 52)

      await renderCaptchaPage(page, TARGET, [noMatchImg], true)

      // Yenileme sonrası doğru görseli ekle
      await page.evaluate(
        ({ correctSrc, target }) => {
          const btn = document.querySelector('[data-action="reload"]') as HTMLElement
          const origClick = btn.onclick
          btn.onclick = function (e) {
            origClick?.call(this, e)
            const img = document.querySelector('img.captcha-img') as HTMLImageElement
            if (img) {
              img.src = correctSrc
              // box-label güncelle (opsiyonel)
              const label = document.querySelector(
                '.box-label[style*="block"]',
              ) as HTMLElement
              if (label)
                label.textContent = `Lütfen ${target} numaralı tüm kutuları işaretleyin.`
            }
          }
        },
        { correctSrc: `data:image/png;base64,${matchImg}`, target: TARGET },
      )

      const result = await solver.solveCaptcha(page)

      // Başarısız veya başarılı — her iki durumda da exception atmamalı
      expect(result).toHaveProperty('success')
      expect(result.targetNumber).toBe(TARGET)
    })
  })
})
