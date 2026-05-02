/**
 * Frekans captcha — hedef ile OCR çıktısı arasında Lev.edit-distance ile tolerant eşleşme (≤1).
 */

/** Rakamlardan oluşan tek sırayı çıkarır (BLS karo OCR çıktısı). */
export function sanitizeCaptchaDigits(raw: string): string {
  return String(raw ?? '').replace(/\D/g, '')
}

/** ilk `len` rakam (yüklenen karo genelde 3 hane). */
export function takeFirstDigitRun(value: string, len: number): string {
  const d = sanitizeCaptchaDigits(value)
  if (d.length >= len) return d.slice(0, len)
  return d
}

/**
 * Klasik Levenshtein (asıl hedef + OCR aynı uzunlukta üç hane).
 */
export function levenshteinDistance(a: string, b: string): number {
  if (a === b) return 0
  if (!a.length) return b.length
  if (!b.length) return a.length
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    const curr = [i]
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      curr[j] = Math.min(
        prev[j] + 1,
        curr[j - 1] + 1,
        prev[j - 1] + cost,
      )
    }
    prev = curr
  }
  return prev[b.length]
}

export type CaptchaMatchKind = 'none' | 'exact' | 'fuzzy_high'

export interface CaptchaMatchEval {
  sanitized: string
  dist: number
  kind: CaptchaMatchKind
}

const FUZZY_MAX = 1

/**
 * @param target Üç hane (ör. "606")
 * @param detected Ham OCR
 */
export function evaluateCaptchaOcrMatch(target: string, detected: string): CaptchaMatchEval {
  const tgt = takeFirstDigitRun(target, 3)
  const san = takeFirstDigitRun(detected, 3)
  if (tgt.length !== 3 || san.length !== 3 || !/^\d{3}$/.test(tgt)) {
    return { sanitized: san, dist: 99, kind: 'none' }
  }
  const dist = levenshteinDistance(san, tgt)
  if (dist === 0) return { sanitized: san, dist: 0, kind: 'exact' }
  if (dist <= FUZZY_MAX) return { sanitized: san, dist, kind: 'fuzzy_high' }
  return { sanitized: san, dist, kind: 'none' }
}

/** Terminale — tıklama anında (Playwright / Node). */
export function logCaptchaTileClick(
  target: string,
  read: string,
  kind: Extract<CaptchaMatchKind, 'exact' | 'fuzzy_high'>,
): void {
  const tag = kind === 'fuzzy_high' ? 'Fuzzy: OK' : 'Exact: OK'
  console.info(
    `[CAPTCHA]: Target ${target} matched with ${read} (${tag}). Clicking tile...`,
  )
}

/** @deprecated Yeni kod `logCaptchaTileClick` kullanmalı. */
export function logOcrSmartMatch(
  target: string,
  sanitized: string,
  dist: number,
): void {
  if (dist === 0) logCaptchaTileClick(target, sanitized, 'exact')
  else if (dist === 1) logCaptchaTileClick(target, sanitized, 'fuzzy_high')
}
