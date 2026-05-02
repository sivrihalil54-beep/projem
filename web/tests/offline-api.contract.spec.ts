import { expect, test } from './fixtures'
import { useBackendRouteMock } from './mockConfig'

test.describe('Offline API mock (summary.md + schemas)', () => {
  test.skip(!useBackendRouteMock(), 'USE_REAL_API acik: bu senaryo yalnizca mock modunda calisir.')

  test.beforeEach(async ({ page }) => {
    await page.goto('/')
  })

  test('GET /api/profiles ve /api/profiles/active ProfileRead alanlarini tasir', async ({
    page,
  }) => {
    const list = await page.evaluate(async () => {
      const r = await fetch('/api/profiles')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    })
    expect(Array.isArray(list)).toBe(true)
    expect(list.length).toBeGreaterThan(0)
    const p = list[0]
    expect(typeof p.id).toBe('number')
    expect(typeof p.label).toBe('string')
    expect(typeof p.email).toBe('string')
    expect(typeof p.password).toBe('string')
    expect(typeof p.login_url).toBe('string')
    expect(typeof p.is_active).toBe('boolean')
    expect(typeof p.run_count).toBe('number')
    expect(typeof p.last_error).toBe('string')
    expect(typeof p.last_error_at).toBe('string')
    if (p.proxy != null) {
      expect(p.proxy).toMatchObject({
        id: expect.any(Number),
        scheme: expect.any(String),
        host: expect.any(String),
        port: expect.any(Number),
      })
    }

    const active = await page.evaluate(async () => {
      const r = await fetch('/api/profiles/active')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    })
    expect(active).not.toBeNull()
    expect(active.email).toBeTruthy()
    expect(active.is_active).toBe(true)
  })

  test('GET /api/profiles/active aktif profil yoksa null', async ({ page, backendState }) => {
    for (const pr of backendState.profiles) pr.is_active = false
    await page.reload()
    const active = await page.evaluate(async () => {
      const r = await fetch('/api/profiles/active')
      return r.json()
    })
    expect(active).toBeNull()
    await expect(page.getByTestId('panel-root')).toBeVisible()
  })

  test('GET /api/proxies ProxyRead alanlari', async ({ page }) => {
    const rows = await page.evaluate(async () => {
      const r = await fetch('/api/proxies')
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    })
    expect(Array.isArray(rows)).toBe(true)
    expect(rows.length).toBeGreaterThan(0)
    const px = rows[0]
    expect(px).toMatchObject({
      id: expect.any(Number),
      scheme: expect.any(String),
      host: expect.any(String),
      port: expect.any(Number),
      is_assigned: expect.any(Boolean),
      fail_count: expect.any(Number),
    })
    expect(typeof px.is_active).toBe('boolean')
    expect('last_used_at' in px).toBeTruthy()
  })
})
