import { expect, test } from './fixtures'

test.describe('BLS Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('ana başlık, kök düzen ve profil mock verisi görünür', async ({ page }) => {
    await expect(page.getByRole('heading', { name: /randevu botu/i })).toBeVisible()
    await expect(page.getByTestId('panel-root')).toBeVisible()
    await expect(page.getByRole('tab', { selected: true, name: 'Hesaplar' })).toBeVisible()
    await expect(page.getByTestId('profile-list-section')).toBeVisible()
    await expect(page.getByTestId('profile-row-1')).toBeVisible()
    await expect(page.getByTestId('profile-row-1')).toContainText('qa.demo@example.com')
    await expect(page.getByRole('heading', { name: 'Kayitli hesaplar' })).toBeVisible()
  })

  test('Proxy havuzu sekmesi mock proxy satırını gösterir', async ({ page }) => {
    await page.getByTestId('tab-proxies').click()
    await expect(page.getByRole('tab', { selected: true, name: 'Proxy havuzu' })).toBeVisible()
    await expect(page.getByTestId('proxy-list-section')).toBeVisible()
    await expect(page.getByTestId('proxy-row-1')).toBeVisible()
    await expect(page.getByTestId('proxy-row-1')).toContainText('10.0.0.1')
    await expect(page.getByTestId('proxy-row-1')).toContainText(':8080')
  })

  test('toplu proxy ekle ile yeni mock satırı listelenir', async ({ page }) => {
    await page.getByTestId('tab-proxies').click()
    await page.getByTestId('proxy-bulk-textarea').fill('192.168.55.91:9292')
    await page.getByRole('button', { name: 'Toplu ekle' }).click()
    await expect(page.getByTestId('panel-status')).toContainText('proxy eklendi')
    await expect(page.getByTestId('proxy-row-2')).toBeVisible()
    await expect(page.getByTestId('proxy-row-2')).toContainText('192.168.55.91:9292')
  })

  test('Proxy tum havuzu sil listeyi bosaltir (mock)', async ({ page }) => {
    await page.getByTestId('tab-proxies').click()
    await expect(page.getByTestId('proxy-row-1')).toBeVisible()
    page.once('dialog', (d) => d.accept())
    await page.getByTestId('proxy-bulk-delete-all').click()
    await expect(page.getByTestId('panel-status')).toContainText(/silindi/i)
    // data-testid on ekli satirlar (rol tabanli tablo yapisi yok); XPath kullanilmadi
    await expect(page.locator('[data-testid^="proxy-row-"]')).toHaveCount(0)
    await expect(page.getByText('Henuz proxy yok.')).toBeVisible()
  })

  test('Profil için Botu baslat başarı bildirimi (mock API)', async ({ page }) => {
    await expect(page.getByTestId('profile-row-1')).toBeVisible()
    await page.getByRole('button', { name: 'Botu baslat' }).click()
    await expect(page.getByTestId('panel-status')).toContainText(/mock/)
  })

  test('Profil düzenle modali Kaydet ile kapanır', async ({ page }) => {
    await page.getByRole('button', { name: 'Duzenle' }).click()
    const modal = page.getByTestId('edit-profile-modal')
    await expect(modal.getByRole('heading', { name: 'Hesabi duzenle' })).toBeVisible()
    await modal.getByRole('button', { name: 'Kaydet' }).click()
    await expect(modal).not.toBeVisible()
    await expect(page.getByTestId('panel-status')).toContainText(/guncellendi/i)
  })
})
