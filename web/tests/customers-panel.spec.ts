import { expect, test } from './fixtures'

test.describe('Musteri paneli', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('Musteriler sekmesi tablo ve canli durum sutunu', async ({ page }) => {
    await page.getByTestId('tab-customers').click()
    await expect(page.getByTestId('customers-dashboard')).toBeVisible()
    await expect(page.getByTestId('customer-table')).toBeVisible()
    await expect(page.getByTestId('customer-row-1')).toBeVisible()
    await expect(page.getByTestId('customer-row-1')).toContainText('Ayse')
    await expect(page.getByTestId('live-status-1')).toContainText('Hazır')
  })

  test('Hizli duzenle modali sekmeleri acar', async ({ page }) => {
    await page.getByTestId('tab-customers').click()
    await page.getByTestId('quick-edit-1').click()
    const modal = page.getByTestId('customer-modal')
    await expect(modal).toBeVisible()
    await expect(modal.getByTestId('customer-tab-personal')).toBeVisible()
    await modal.getByTestId('customer-tab-location').click()
    await expect(modal.getByLabel('Jurisdiction ara')).toBeVisible()
    await modal.getByTestId('customer-modal-cancel').click()
    await expect(modal).toBeHidden()
  })

  test('GET /api/customer mock verisi (sayfa fetch ile) sablon testiyle uyumlu', async ({
    page,
  }) => {
    await page.goto('/')
    const j = await page.evaluate(async () => {
      const r = await fetch('/api/customer/1')
      if (!r.ok) return null
      return r.json() as Promise<{ id: number; first_name: string }>
    })
    expect(j).not.toBeNull()
    if (!j) return
    expect(j.id).toBe(1)
    expect(j.first_name.length).toBeGreaterThan(0)
  })
})
