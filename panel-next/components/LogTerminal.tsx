'use client'

import { fetchBotLogs, fetchBotStatus, getApiBase } from '@/lib/api'
import type { BotLogsResponse } from '@/lib/types'
import { useCallback, useEffect, useRef, useState } from 'react'

type Props = {
  autoFollow?: boolean
  className?: string
  testId?: string
}

export function LogTerminal({
  autoFollow = true,
  className = '',
  testId = 'bot-log-terminal',
}: Props) {
  const [text, setText] = useState('')
  const [lineStatus, setLineStatus] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const preRef = useRef<HTMLPreElement>(null)
  const offsetRef = useRef(0)
  const api = getApiBase()

  const pollOnce = useCallback(async () => {
    try {
      const st = await fetchBotStatus()
      setLineStatus(
        st.running
          ? `Bot çalışıyor (pid ${st.pid ?? '—'})`
          : 'Bot bekleniyor / kapalı',
      )
    } catch {
      setLineStatus('Durum okunamadı')
    }

    try {
      const curOff = offsetRef.current
      const data: BotLogsResponse = await fetchBotLogs(curOff, 'follow')
      setErr(null)
      if (data.seek_reset) {
        offsetRef.current = data.next_offset
      }
      if (data.chunk) {
        setText((t) => t + data.chunk)
      }
      offsetRef.current = data.next_offset
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Log alınamadı')
    }
  }, [])

  useEffect(() => {
    if (!autoFollow) return
    const id = window.setInterval(() => {
      void pollOnce()
    }, 1200)
    return () => window.clearInterval(id)
  }, [autoFollow, pollOnce])

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [text])

  return (
    <div
      className={`flex flex-col gap-2 rounded-lg border border-zinc-700 bg-zinc-950/80 ${className}`}
      data-testid={testId}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 px-3 py-2">
        <span
          className="text-xs font-medium text-zinc-400"
          data-testid={`${testId}-status`}
        >
          {lineStatus}
        </span>
        <span className="font-mono text-[10px] text-zinc-500">{api}</span>
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded-md border border-zinc-600 bg-zinc-800 px-2 py-1 text-xs text-zinc-100 hover:bg-zinc-700"
            onClick={() => {
              setText('')
              offsetRef.current = 0
            }}
            aria-label="Log ekranını temizle"
          >
            Temizle
          </button>
          <button
            type="button"
            className="rounded-md border border-emerald-800 bg-emerald-950/50 px-2 py-1 text-xs text-emerald-200 hover:bg-emerald-900/40"
            onClick={() => void pollOnce()}
            aria-label="Logları yenile"
          >
            Yenile
          </button>
        </div>
      </div>
      {err ? (
        <p className="px-3 text-xs text-red-400" data-testid={`${testId}-error`}>
          {err}
        </p>
      ) : null}
      <pre
        ref={preRef}
        className="max-h-64 overflow-auto px-3 pb-3 font-mono text-[11px] leading-relaxed text-emerald-100/90"
        data-testid={`${testId}-output`}
      >
        {text || '… çıktı bekleniyor (bot başlatıldığında burada görünür)'}
      </pre>
    </div>
  )
}
