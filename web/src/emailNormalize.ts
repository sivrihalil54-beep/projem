/** E-posta: bosluk + zero-width (yapistirma) — backend utils/email_normalize ile ayni mantik */

const ZERO_WIDTH_OR_BREAK = /[\u200B-\u200D\uFEFF\r\n]+/g

export function normalizeEmail(raw: string): string {
  return raw.replace(ZERO_WIDTH_OR_BREAK, '').trim()
}
