import { parseProxyLine } from '@/lib/proxyParse'
import net from 'net'
import { NextResponse } from 'next/server'

export async function POST(req: Request) {
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'Geçersiz JSON' }, { status: 400 })
  }
  const line =
    typeof body === 'object' &&
    body !== null &&
    'line' in body &&
    typeof (body as { line: unknown }).line === 'string'
      ? (body as { line: string }).line
      : ''
  const parsed = parseProxyLine(line)
  if (!parsed) {
    return NextResponse.json(
      { ok: false, error: 'Satır ayrıştırılamadı (host:port veya ip:port:user:pass).' },
      { status: 400 },
    )
  }

  const ok = await new Promise<boolean>((resolve) => {
    const socket = net.createConnection(
      { host: parsed.host, port: parsed.port, timeout: 12_000 },
      () => {
        socket.end()
        resolve(true)
      },
    )
    socket.on('error', () => resolve(false))
    socket.on('timeout', () => {
      try {
        socket.destroy()
      } catch {
        /* ignore */
      }
      resolve(false)
    })
  })

  if (!ok) {
    return NextResponse.json(
      {
        ok: false,
        error: `TCP bağlantısı kurulamadı: ${parsed.host}:${parsed.port}`,
        host: parsed.host,
        port: parsed.port,
      },
      { status: 502 },
    )
  }

  return NextResponse.json({
    ok: true,
    host: parsed.host,
    port: parsed.port,
  })
}
