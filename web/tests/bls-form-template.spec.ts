import { expect, test } from './fixtures'
import { getCustomerFromApiInPage } from './customerApi'

/**
 * Ornek entegrasyon: panel API'den musteri oku, BLS benzeri forma yaz.
 * Gercek BLS URL'si yerine deterministik bir mini-HTML kullanilir.
 */
test('BLS Form Doldurma — panel musteri verisi ile', async ({ page }) => {
  await page.goto('/')
  const customer = await getCustomerFromApiInPage(page, 1)

  await page.setContent(`
    <!doctype html>
    <html lang="tr">
      <body>
        <main>
          <label for="firstName">First Name</label>
          <input id="firstName" name="firstName" />
          <label for="lastName">Last Name</label>
          <input id="lastName" name="lastName" />
          <button type="submit">Devam Et</button>
        </main>
      </body>
    </html>
  `)

  await page.getByLabel('First Name').fill(customer.first_name)
  await page.getByLabel('Last Name').fill(customer.last_name)
  await expect(page.getByRole('button', { name: 'Devam Et' })).toBeEnabled()
})
