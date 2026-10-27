'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import type { FitAddon } from '@xterm/addon-fit'
import type { ITheme, Terminal as XTerm } from '@xterm/xterm'
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  CircleSlash,
  Clock3,
  Lock,
  Power,
  Radio,
  RefreshCw,
  Rows3,
  ShieldAlert,
  ShieldCheck,
  TerminalSquare,
  Timer,
  WifiOff,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export type ConnState = 'connecting' | 'live' | 'closed' | 'denied' | 'error'

const STATUS_META: Record<ConnState, { label: string; tone: string; dot: string }> = {
  connecting: {
    label: 'negotiating',
    tone: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    dot: 'bg-amber-400',
  },
  live: {
    label: 'live',
    tone: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    dot: 'bg-emerald-400',
  },
  closed: {
    label: 'closed',
    tone: 'border-zinc-700 bg-zinc-800/60 text-zinc-400',
    dot: 'bg-zinc-500',
  },
  denied: {
    label: 'denied · 1008',
    tone: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    dot: 'bg-rose-500',
  },
  error: {
    label: 'transport error',
    tone: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
    dot: 'bg-rose-400',
  },
}

/** Neon terminal palette, tuned to sit on the dashboard's #05070a surface. */
const TERMINAL_THEME: ITheme = {
  background: '#05070a',
  foreground: '#d7f7e7',
  cursor: '#34d399',
  cursorAccent: '#05070a',
  selectionBackground: 'rgba(52, 211, 153, 0.28)',
  black: '#10151c',
  red: '#fb7185',
  green: '#34d399',
  yellow: '#fbbf24',
  blue: '#60a5fa',
  magenta: '#c084fc',
  cyan: '#22d3ee',
  white: '#e4e4e7',
  brightBlack: '#52525b',
  brightRed: '#fda4af',
  brightGreen: '#6ee7b7',
  brightYellow: '#fde68a',
  brightBlue: '#93c5fd',
  brightMagenta: '#d8b4fe',
  brightCyan: '#a5f3fc',
  brightWhite: '#fafafa',
}

const QUICK_COMMANDS = [
  { label: 'uptime', command: 'uptime' },
  { label: 'df -h', command: 'df -h' },
  { label: 'free -m', command: 'free -m' },
  { label: 'ss -tulpn', command: 'ss -tulpn | head -20' },
  { label: 'clear', command: 'clear' },
]

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`
}

function formatDuration(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':')
}

interface TerminalConsoleProps {
  token: string
  apiUrl: string
  /** Reports connection lifecycle changes to the page chrome. */
  onStatus: (status: ConnState) => void
  /** Drops the elevated token and returns to the elevation gate. */
  onLock: () => void
}

/**
 * The privileged command pane.
 *
 * Owns the xterm.js emulator and the `/api/terminal/ws` socket:
 * - keystrokes are JSON text frames (`{ type: "input", data }`)
 * - geometry changes are JSON text frames (`{ type: "resize", rows, cols }`)
 * - PTY output arrives as binary frames and is written straight to the emulator
 *
 * A close with code 1008 means the control plane rejected the token; that is
 * surfaced as the `denied` state rather than a generic disconnect.
 */
export function TerminalConsole({ token, apiUrl, onStatus, onLock }: TerminalConsoleProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const onStatusRef = useRef(onStatus)
  const countersRef = useRef({ bytesIn: 0, bytesOut: 0 })
  const startedAtRef = useRef<number>(0)
  const terminatedRef = useRef(false)

  const [conn, setConn] = useState<ConnState>('connecting')
  const [sessionId, setSessionId] = useState(0)
  const [terminatedByOperator, setTerminatedByOperator] = useState(false)
  const [dims, setDims] = useState({ cols: 0, rows: 0 })
  const [telemetry, setTelemetry] = useState({ bytesIn: 0, bytesOut: 0 })
  const [elapsed, setElapsed] = useState(0)

  const status = STATUS_META[conn]
  const tokenFingerprint = useMemo(() => {
    // Never render the token itself — just a short stable hint for the operator.
    let hash = 0
    for (let index = 0; index < token.length; index += 1) {
      hash = (hash * 31 + token.charCodeAt(index)) % 0xffff
    }
    return hash.toString(16).padStart(4, '0')
  }, [token])

  useEffect(() => {
    onStatusRef.current = onStatus
  }, [onStatus])

  /* -------------------------------------------------------------- telemetry */
  useEffect(() => {
    const id = window.setInterval(() => {
      setTelemetry({
        bytesIn: countersRef.current.bytesIn,
        bytesOut: countersRef.current.bytesOut,
      })
      setElapsed(Date.now() - startedAtRef.current)
    }, 500)
    return () => window.clearInterval(id)
  }, [])

  /* ------------------------------------------------ terminal + websocket wire */
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let disposed = false
    let term: XTerm | null = null
    let fit: FitAddon | null = null
    let socket: WebSocket | null = null
    let observer: ResizeObserver | null = null
    let resizeTimer: ReturnType<typeof setTimeout> | null = null

    countersRef.current = { bytesIn: 0, bytesOut: 0 }
    startedAtRef.current = Date.now()
    setTelemetry({ bytesIn: 0, bytesOut: 0 })
    setElapsed(0)
    setDims({ cols: 0, rows: 0 })
    setTerminatedByOperator(false)
    setConn('connecting')
    onStatusRef.current('connecting')

    // Drop any emulator left behind by a previous session.
    container.innerHTML = ''

    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
      ])
      if (disposed || !containerRef.current) return

      const emulator = new Terminal({
        fontFamily:
          'var(--font-geist-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        fontSize: 13,
        lineHeight: 1.35,
        letterSpacing: 0.3,
        cursorBlink: true,
        cursorStyle: 'bar',
        scrollback: 8000,
        allowTransparency: true,
        theme: TERMINAL_THEME,
      })
      const fitAddon = new FitAddon()

      term = emulator
      fit = fitAddon

      emulator.loadAddon(fitAddon)
      emulator.open(container)

      const applyFit = () => {
        try {
          fitAddon.fit()
        } catch {
          /* container not measurable yet — the observer retries */
        }
      }
      applyFit()
      setDims({ cols: emulator.cols, rows: emulator.rows })
      emulator.focus()

      emulator.writeln(
        '\x1b[38;5;42m⟢ NOC command pane — requesting privileged PTY…\x1b[0m'
      )

      const send = (payload: unknown) => {
        if (!socket || socket.readyState !== WebSocket.OPEN) return false
        socket.send(JSON.stringify(payload))
        return true
      }

      const sendResize = () => {
        if (!term) return
        send({ type: 'resize', rows: term.rows, cols: term.cols })
      }

      /* ---- socket ------------------------------------------------------- */
      const base = apiUrl.replace(/\/+$/, '').replace(/^http/, 'ws')
      const url = `${base}/api/terminal/ws?token=${encodeURIComponent(token)}`
      const ws = new WebSocket(url)
      ws.binaryType = 'arraybuffer'
      socket = ws
      socketRef.current = ws

      ws.onopen = () => {
        if (disposed) return
        setConn('live')
        onStatusRef.current('live')
        sendResize()
        emulator.focus()
      }

      ws.onmessage = (event: MessageEvent) => {
        if (disposed) return
        if (typeof event.data === 'string') {
          countersRef.current.bytesIn += event.data.length
          emulator.write(event.data)
          return
        }
        const bytes = new Uint8Array(event.data as ArrayBuffer)
        countersRef.current.bytesIn += bytes.byteLength
        emulator.write(bytes)
      }

      ws.onerror = () => {
        if (disposed) return
        setConn('error')
        onStatusRef.current('error')
      }

      ws.onclose = (event: CloseEvent) => {
        if (disposed) return
        socketRef.current = null
        if (event.code === 1008) {
          setConn('denied')
          onStatusRef.current('denied')
          emulator.writeln(
            '\r\n\x1b[38;5;203m✖ access denied — control plane rejected the token (code 1008)\x1b[0m'
          )
        } else if (terminatedRef.current) {
          setConn('closed')
          onStatusRef.current('closed')
          emulator.writeln('\r\n\x1b[38;5;244m■ session terminated by operator\x1b[0m')
        } else {
          setConn('closed')
          onStatusRef.current('closed')
          emulator.writeln(
            `\r\n\x1b[38;5;244m■ session closed — code ${event.code}${
              event.reason ? ` (${event.reason})` : ''
            }\x1b[0m`
          )
        }
      }

      /* ---- emulator → socket -------------------------------------------- */
      emulator.onData((data) => {
        countersRef.current.bytesOut += data.length
        send({ type: 'input', data })
      })

      emulator.onResize(({ cols, rows }) => {
        setDims({ cols, rows })
        send({ type: 'resize', rows, cols })
      })

      /* ---- container → emulator geometry -------------------------------- */
      observer = new ResizeObserver(() => {
        if (resizeTimer) clearTimeout(resizeTimer)
        resizeTimer = setTimeout(applyFit, 120)
      })
      observer.observe(container)
    })()

    return () => {
      disposed = true
      terminatedRef.current = false
      if (resizeTimer) clearTimeout(resizeTimer)
      observer?.disconnect()
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close(1000, 'console unmounted')
        }
      }
      socketRef.current = null
      try {
        fit?.dispose()
      } catch {
        /* addon already torn down by the emulator */
      }
      term?.dispose()
      if (container) container.innerHTML = ''
    }
  }, [apiUrl, token, sessionId])

  /* ------------------------------------------------------------- operators */
  const terminate = useCallback(() => {
    terminatedRef.current = true
    const socket = socketRef.current
    setConn('closed')
    onStatusRef.current('closed')
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      socket.close(1000, 'operator kill switch')
    }
  }, [])

  const reconnect = useCallback(() => {
    setSessionId((value) => value + 1)
  }, [])

  const sendCommand = useCallback((command: string) => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'input', data: `${command}\r` }))
  }, [])

  const panelState = terminatedByOperator ? 'terminated' : conn
  const showOverlay = conn !== 'live'

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, filter: 'blur(6px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, y: -14, filter: 'blur(8px)' }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      className="noc-pane noc-rim relative w-full min-w-0 max-w-full overflow-hidden rounded-2xl border border-zinc-800/90 bg-[#05070a]"
    >
      {/* ------------------------------- title bar ------------------------- */}
      <div className="relative flex flex-wrap items-center gap-3 border-b border-zinc-800/80 bg-zinc-950/60 px-4 py-2.5">
        <div className="flex items-center gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-rose-500/70" />
          <span className="size-2.5 rounded-full bg-amber-400/70" />
          <span className="size-2.5 rounded-full bg-emerald-500/80" />
        </div>

        <div className="ml-2 flex min-w-0 items-center gap-2 font-mono text-xs">
          <TerminalSquare className="size-3.5 text-emerald-400/80" />
          <span className="truncate text-zinc-300">operator@noc-core</span>
          <span className="text-zinc-600">:</span>
          <span className="text-emerald-300/80">~</span>
          <span className="hidden truncate text-zinc-600 sm:inline">· privileged pty</span>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <span
            aria-live="polite"
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.18em]',
              status.tone
            )}
          >
            <span
              className={cn(
                'size-1.5 rounded-full',
                status.dot,
                conn === 'live' && 'noc-blink'
              )}
            />
            {status.label}
          </span>

          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={reconnect}
            className="h-7 gap-1.5 px-2 text-[11px] text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-200"
          >
            <RefreshCw className="size-3.5" />
            <span className="hidden sm:inline">respawn</span>
          </Button>

          <Button
            type="button"
            size="sm"
            onClick={terminate}
            disabled={conn === 'closed' || conn === 'denied' || conn === 'error'}
            className="h-7 gap-1.5 bg-rose-500/15 px-2.5 text-[11px] font-semibold tracking-wide text-rose-300 hover:bg-rose-500/25 disabled:opacity-40 noc-glow-danger"
          >
            <Power className="size-3.5" />
            kill switch
          </Button>

          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            onClick={onLock}
            aria-label="Lock console and return to the elevation gate"
            className="text-zinc-500 hover:bg-zinc-800/70 hover:text-zinc-200"
          >
            <Lock className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* ------------------------------- terminal -------------------------- */}
      <div className="relative">
        <div
          ref={containerRef}
          onClick={() =>
            containerRef.current?.querySelector<HTMLTextAreaElement>('textarea')?.focus()
          }
          className="noc-xterm noc-crt relative h-[clamp(340px,52vh,560px)] overflow-hidden bg-[#05070a] px-3 py-2.5"
        />
        <div aria-hidden className="noc-sweep" />

        <AnimatePresence>
          {showOverlay ? (
            <motion.div
              key={panelState}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-[#05070a]/85 px-6 text-center backdrop-blur-[2px]"
            >
              {conn === 'connecting' ? (
                <>
                  <div className="relative flex size-12 items-center justify-center">
                    <span className="absolute inset-0 animate-spin rounded-full border-2 border-emerald-500/20 border-t-emerald-400" />
                    <Radio className="size-5 text-emerald-400/80" />
                  </div>
                  <div>
                    <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-emerald-400/80">
                      negotiating pty bridge
                    </p>
                    <p className="mt-2 text-xs text-zinc-500">
                      Authenticating token and forking the shell…
                    </p>
                  </div>
                </>
              ) : null}

              {conn === 'denied' ? (
                <>
                  <div className="flex size-12 items-center justify-center rounded-full border border-rose-500/40 bg-rose-500/10 noc-glow-danger">
                    <ShieldAlert className="size-5 text-rose-400" />
                  </div>
                  <div>
                    <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-rose-300">
                      access denied · 1008
                    </p>
                    <p className="mt-2 max-w-sm text-xs leading-relaxed text-zinc-400">
                      The control plane rejected the operator token. No PTY was allocated. Re-enter
                      credentials or respawn with a valid token.
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      onClick={onLock}
                      className="bg-rose-500/15 text-rose-200 hover:bg-rose-500/25"
                    >
                      <Lock className="size-3.5" />
                      re-authenticate
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={reconnect}>
                      <RefreshCw className="size-3.5" />
                      retry handshake
                    </Button>
                  </div>
                </>
              ) : null}

              {conn === 'closed' || conn === 'error' ? (
                <>
                  <div className="flex size-12 items-center justify-center rounded-full border border-zinc-700 bg-zinc-900/70">
                    {terminatedByOperator ? (
                      <CircleSlash className="size-5 text-rose-400/80" />
                    ) : (
                      <WifiOff className="size-5 text-zinc-400" />
                    )}
                  </div>
                  <div>
                    <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-zinc-400">
                      {terminatedByOperator
                        ? 'session terminated'
                        : conn === 'error'
                          ? 'transport error'
                          : 'session closed'}
                    </p>
                    <p className="mt-2 max-w-sm text-xs leading-relaxed text-zinc-500">
                      {terminatedByOperator
                        ? 'The operator kill switch closed the PTY. Scrollback is preserved above until you respawn.'
                        : 'The PTY stream ended. The shell may have exited, or the control plane dropped the socket.'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      onClick={reconnect}
                      className="bg-emerald-500 text-emerald-950 hover:bg-emerald-400"
                    >
                      <RefreshCw className="size-3.5" />
                      respawn session
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={onLock}>
                      <Lock className="size-3.5" />
                      return to gate
                    </Button>
                  </div>
                </>
              ) : null}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      {/* ------------------------------- telemetry rail -------------------- */}
      <div className="relative flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-zinc-800/80 bg-zinc-950/60 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.16em] text-zinc-500">
        <span className="flex items-center gap-1.5">
          <Rows3 className="size-3 text-emerald-400/70" />
          {dims.cols}×{dims.rows}
        </span>
        <span className="flex items-center gap-1.5">
          <ArrowDownToLine className="size-3 text-emerald-400/70" />
          in {formatBytes(telemetry.bytesIn)}
        </span>
        <span className="flex items-center gap-1.5">
          <ArrowUpFromLine className="size-3 text-sky-400/70" />
          out {formatBytes(telemetry.bytesOut)}
        </span>
        <span className="flex items-center gap-1.5">
          <Timer className="size-3 text-zinc-500" />
          {formatDuration(elapsed)}
        </span>
        <span className="hidden items-center gap-1.5 md:flex">
          <ShieldCheck className="size-3 text-emerald-400/70" />
          admin · fp&nbsp;{tokenFingerprint}
        </span>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Clock3 className="size-3 text-zinc-600" />
          {QUICK_COMMANDS.map((entry) => (
            <button
              key={entry.label}
              type="button"
              onClick={() => sendCommand(entry.command)}
              disabled={conn !== 'live'}
              className="rounded border border-zinc-800 bg-zinc-900/70 px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-zinc-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>
    </motion.div>
  )
}
