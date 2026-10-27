'use client'

import { useCallback, useState, useSyncExternalStore } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { KeyRound, Lock, ShieldHalf, Terminal as TerminalIcon } from 'lucide-react'

import { ElevationGate } from '@/components/terminal/elevation-gate'
import { TerminalConsole, type ConnState } from '@/components/terminal/terminal-console'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import './terminal.css'
import '@xterm/xterm/css/xterm.css'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const TOKEN_STORAGE_KEY = 'noc.terminal.operator-token'

/**
 * sessionStorage is an external store: read it through useSyncExternalStore so
 * the elevation hint survives a refresh without a setState-in-effect cascade.
 */
const subscribeToStorage = () => () => {}

function readStoredToken() {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

const STATUS_ACCENT: Record<ConnState | 'locked', string> = {
  locked: 'border-zinc-700 bg-zinc-800/50 text-zinc-400',
  connecting: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  live: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  closed: 'border-zinc-700 bg-zinc-800/50 text-zinc-400',
  denied: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  error: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
}

/**
 * Command Execution Pane — the flagship privileged surface of the NOC.
 *
 * Two phases, both animated:
 * 1. `locked` — the elevation gate asks for the operator token before anything
 *    touches the network.
 * 2. `elevated` — the xterm.js console opens `/api/terminal/ws?token=…` against
 *    the FastAPI PTY bridge with bidirectional I/O and window-size negotiation.
 *
 * The token lives in React state only; with "keep for this tab" it is mirrored
 * into sessionStorage so a refresh does not lose the elevation.
 */
export default function TerminalPage() {
  const [token, setToken] = useState<string | null>(null)
  const storedToken = useSyncExternalStore(subscribeToStorage, readStoredToken, () => '')
  const [status, setStatus] = useState<ConnState | 'locked'>('locked')
  const [notice, setNotice] = useState<string | null>(null)

  const elevate = useCallback((value: string, remember: boolean) => {
    try {
      if (remember) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, value)
      else window.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
    } catch {
      /* ignore storage failures */
    }
    setNotice(null)
    setStatus('connecting')
    setToken(value)
  }, [])

  const lock = useCallback(() => {
    setToken(null)
    setStatus('locked')
    try {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY)
    } catch {
      /* ignore storage failures */
    }
  }, [])

  const handleStatus = useCallback((next: ConnState) => {
    setStatus(next)
    if (next === 'denied') {
      setNotice('Control plane rejected that token (close code 1008). Confirm the value of TERMINAL_ACCESS_TOKEN.')
    }
    if (next === 'live') {
      setNotice(null)
    }
  }, [])

  const accent = STATUS_ACCENT[status]

  return (
    <div className="min-w-0 space-y-5">
      {/* ------------------------------ page header ------------------------ */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5 font-mono text-[10px] uppercase tracking-[0.34em] text-emerald-400/80">
            <span className="relative flex size-2 items-center justify-center">
              <span className="absolute inset-0 rounded-full border border-emerald-500/50" />
              <span className="size-1 rounded-full bg-emerald-400" />
            </span>
            privileged surface · administrative access
          </div>
          <h1 className="mt-2 flex items-center gap-3 text-3xl font-bold tracking-tight text-zinc-100">
            Command Execution Pane
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-zinc-500">
            Live shell on the NOC control plane over an authenticated PTY websocket. Keystrokes
            upstream, raw terminal bytes downstream, geometry negotiated on every resize.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em]',
              accent
            )}
          >
            <span className="relative flex size-1.5">
              <span
                className={cn(
                  'size-1.5 rounded-full',
                  status === 'live'
                    ? 'bg-emerald-400 noc-blink'
                    : status === 'denied' || status === 'error'
                      ? 'bg-rose-400'
                      : status === 'connecting'
                        ? 'bg-amber-400 noc-blink'
                        : 'bg-zinc-500'
                )}
              />
            </span>
            {status === 'locked' ? 'awaiting elevation' : status}
          </span>

          <span className="hidden items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-400 sm:inline-flex">
            <ShieldHalf className="size-3 text-emerald-400/80" />
            sudo · root
          </span>

          {token ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={lock}
              className="h-8 gap-1.5 border border-zinc-800 px-2.5 text-[11px] text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-200"
            >
              <Lock className="size-3.5" />
              lock console
            </Button>
          ) : null}
        </div>
      </div>

      {/* ------------------------------ phases ---------------------------- */}
      <AnimatePresence mode="wait" initial={false}>
        {token ? (
          <TerminalConsole
            key="console"
            token={token}
            apiUrl={API_URL}
            onStatus={handleStatus}
            onLock={lock}
          />
        ) : (
          <div key="gate" className="space-y-5">
            <ElevationGate onElevate={elevate} initialToken={storedToken} notice={notice} />

            {/* -------------------------- protocol reference ---------------- */}
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15, duration: 0.45 }}
              className="grid gap-4 md:grid-cols-3"
            >
              {[
                {
                  icon: TerminalIcon,
                  title: 'Real emulation',
                  body: 'xterm.js renders a true PTY, so top, vim and tmux draw exactly as they would over SSH.',
                },
                {
                  icon: KeyRound,
                  title: 'Token handshake',
                  body: 'The token rides the websocket query string and is validated server-side before a shell is forked.',
                },
                {
                  icon: Lock,
                  title: 'Kill switch',
                  body: 'One control drops the socket, tears down the PTY and preserves scrollback for the post-mortem.',
                },
              ].map((item) => (
                <div
                  key={item.title}
                  className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 transition-colors hover:border-emerald-500/25"
                >
                  <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                    <item.icon className="size-4 text-emerald-400/80" strokeWidth={1.7} />
                    {item.title}
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">{item.body}</p>
                </div>
              ))}
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  )
}
