import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

import type { Page, Response, TestInfo } from '@playwright/test'

import { resolveRepositoryRoot } from './projectPaths'

function sanitizeSlug(raw: string, maxLen: number): string {
  const cleaned = raw.replace(/[^\w\-]+/g, '_').replace(/^_+|_+$/g, '')
  return (cleaned || 'failure').slice(0, maxLen)
}

/** Son N istek özeti Playwright dinleyiciden (status + URL). */
export type FailureNetworkProbe = {
  finalize: () => Array<{ url: string; status: number; method: string }>
}

/**
 * Sekme yaşamı için yanıt logu — test başına bir kez `start`; finalize failure'da.
 */
export function startFailureNetworkProbe(page: Page, maxEntries = 500): FailureNetworkProbe {
  const rows: Array<{ url: string; status: number; method: string }> = []
  const fn = (res: Response) => {
    try {
      rows.push({
        url: res.url(),
        status: res.status(),
        method: res.request().method(),
      })
      if (rows.length > maxEntries) rows.shift()
    } catch {
      /* noop */
    }
  }
  page.on('response', fn)
  return {
    finalize: () => {
      try {
        page.off('response', fn)
      } catch {
        /* noop */
      }
      return [...rows]
    },
  }
}

async function writeFailureSidecars(
  page: Page,
  basePathWithoutExt: string,
  networkRows: Array<{ url: string; status: number; method: string }>,
): Promise<void> {
  let dom = ''
  try {
    dom = await page.content()
  } catch {
    dom = '<!-- dom capture failed -->\n'
  }
  let aria: unknown = null
  try {
    const pa = (
      page as unknown as {
        accessibility: { snapshot: (options?: { interestingOnly?: boolean }) => unknown }
      }
    ).accessibility
    aria = await pa.snapshot({ interestingOnly: true })
  } catch {
    aria = { error: 'accessibility_snapshot_failed' }
  }
  let perf: unknown = null
  try {
    perf = await page.evaluate(() => {
      const arr = performance.getEntriesByType('resource') as PerformanceResourceTiming[]
      return arr.slice(Math.max(0, arr.length - 350)).map((e) => ({
        name: e.name,
        initiatorType: e.initiatorType,
        duration: e.duration,
        transferSize: e.transferSize ?? null,
      }))
    })
  } catch {
    perf = { error: 'performance_timeline_failed' }
  }
  let pageUrl = ''
  try {
    pageUrl = page.url()
  } catch {
    pageUrl = ''
  }
  await writeFile(
    `${basePathWithoutExt}_dom.html`,
    `<!-- Failure DOM snapshot | ${pageUrl} -->\n${dom}`,
    'utf8',
  )
  await writeFile(
    `${basePathWithoutExt}_aria.json`,
    JSON.stringify(aria, null, 2),
    'utf8',
  )
  await writeFile(
    `${basePathWithoutExt}_network.json`,
    JSON.stringify(
      { pageUrl, responses: networkRows, resourceSamples: perf },
      null,
      2,
    ),
    'utf8',
  )
}

/**
 * Başarısız / zaman aşımı testlerinde tam sayfa PNG — kök `dataset/failures/`.
 * İsteğe bağlı: yanıt dinleyicisi ile DOM + erişilebilirlik ağacı + ağ özetleri.
 */
export async function savePlaywrightFailureScreenshot(
  page: Page,
  testInfo: Pick<TestInfo, 'title' | 'retry'>,
  extraSlug?: string,
  networkProbe?: FailureNetworkProbe,
): Promise<string | null> {
  const root = resolveRepositoryRoot()
  const dir = path.join(root, 'dataset', 'failures')
  await mkdir(dir, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  const titlePart = sanitizeSlug(testInfo.title, 96)
  const extra = extraSlug ? `_${sanitizeSlug(extraSlug, 48)}` : ''
  const file = path.join(
    dir,
    `${ts}_r${testInfo.retry}${extra}_${titlePart}.png`,
  )
  const baseSans = file.replace(/\.png$/i, '')
  const netRows = networkProbe?.finalize() ?? []
  try {
    await page.screenshot({ path: file, fullPage: true, timeout: 18_000 })
    try {
      await writeFailureSidecars(page, baseSans, netRows)
    } catch {
      /* sidecar best-effort */
    }
    return file
  } catch {
    try {
      await writeFailureSidecars(page, baseSans, netRows)
    } catch {
      /* noop */
    }
    return null
  }
}

/**
 * Flow / guard içinden (TestInfo olmadan) PNG + DOM + ağ özeti.
 */
export async function saveLoginFlowFailureScreenshot(
  page: Page,
  stage: string,
  networkProbe?: FailureNetworkProbe,
): Promise<string | null> {
  const root = resolveRepositoryRoot()
  const dir = path.join(root, 'dataset', 'failures')
  await mkdir(dir, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-')
  const file = path.join(dir, `${ts}_${sanitizeSlug(stage, 72)}_flow.png`)
  const baseSans = file.replace(/\.png$/i, '')
  const netRows = networkProbe?.finalize() ?? []
  try {
    await page.screenshot({ path: file, fullPage: true, timeout: 18_000 })
    try {
      await writeFailureSidecars(page, baseSans, netRows)
    } catch {
      /* noop */
    }
    return file
  } catch {
    try {
      await writeFailureSidecars(page, baseSans, netRows)
    } catch {
      /* noop */
    }
    return null
  }
}
