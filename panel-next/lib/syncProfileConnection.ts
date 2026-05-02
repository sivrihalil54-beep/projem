import { parseProxyLine } from '@/lib/proxyParse'
import {
  assignProxyToProfile,
  importProxies,
  listProxyPool,
  updateProfileGmail,
} from '@/lib/api'

export async function syncProfileConnection(
  profileId: number,
  gmailAppPassword: string,
  proxyLineRaw: string,
): Promise<void> {
  const gap = gmailAppPassword.trim()
  if (gap) {
    await updateProfileGmail(profileId, gap)
  }

  const line = proxyLineRaw.trim()
  if (!line) return

  const parsed = parseProxyLine(line)
  if (!parsed) {
    throw new Error('Proxy satırı ayrıştırılamadı (host:port veya ip:port:user:pass).')
  }

  await importProxies(line)
  const pool = await listProxyPool()
  const match = pool.find(
    (p) =>
      p.host === parsed.host &&
      p.port === parsed.port &&
      (p.username || '').trim() === parsed.username &&
      (p.password || '').trim() === parsed.password,
  )
  if (!match) {
    throw new Error(
      'Proxy havuza eklendi ancak eşleşen kayıt bulunamadı; /api/proxies listesini kontrol edin.',
    )
  }
  await assignProxyToProfile(profileId, match.id)
}
