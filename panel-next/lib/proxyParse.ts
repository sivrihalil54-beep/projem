/** `backend/parse_proxy_bulk.parse_proxy_line` ile aynı ip:port:user:pass ayrıştırması (TCP test). */

export type ParsedProxyLine = {
  host: string
  port: number
  username: string
  password: string
}

export function parseProxyLine(line: string): ParsedProxyLine | null {
  const raw = line.trim()
  if (!raw || raw.startsWith('#')) return null
  if (raw.includes('://')) {
    try {
      const u = new URL(raw)
      const host = (u.hostname || '').trim()
      let port = u.port ? parseInt(u.port, 10) : NaN
      if (!host) return null
      if (!Number.isFinite(port)) {
        port = raw.toLowerCase().includes('socks') ? 1080 : 80
      }
      return {
        host,
        port,
        username: (u.username || '').trim(),
        password: u.password || '',
      }
    } catch {
      return null
    }
  }
  const parts = raw.split(':')
  if (
    parts.length === 4 &&
    !raw.includes('@') &&
    !raw.trim().startsWith('[')
  ) {
    const [hostP, portS, username, passwd] = parts
    const port = parseInt(portS.trim(), 10)
    if (!hostP.trim() || !Number.isFinite(port)) return null
    return {
      host: hostP.trim(),
      port,
      username: username.trim(),
      password: passwd.trim(),
    }
  }
  if (raw.includes('@')) {
    const at = raw.lastIndexOf('@')
    const auth = raw.slice(0, at)
    const hostport = raw.slice(at + 1)
    if (!auth.includes(':') || !hostport.includes(':')) return null
    const colonAuth = auth.indexOf(':')
    const user = auth.slice(0, colonAuth).trim()
    const pw = auth.slice(colonAuth + 1).trim()
    const colonHp = hostport.lastIndexOf(':')
    const h = hostport.slice(0, colonHp).trim()
    const p = parseInt(hostport.slice(colonHp + 1).trim(), 10)
    if (!h || !Number.isFinite(p)) return null
    return { host: h, port: p, username: user, password: pw }
  }
  const colon = raw.lastIndexOf(':')
  if (colon <= 0) return null
  const hostOnly = raw.slice(0, colon).trim()
  const port = parseInt(raw.slice(colon + 1).trim(), 10)
  if (!hostOnly || !Number.isFinite(port)) return null
  return { host: hostOnly, port, username: '', password: '' }
}
