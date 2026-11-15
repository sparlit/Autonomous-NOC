'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowRight,
  Eye,
  EyeOff,
  Fingerprint,
  KeyRound,
  Lock,
  ShieldAlert,
  ShieldCheck,
  Terminal as TerminalIcon,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const POLICY = [
  {
    icon: ShieldAlert,
    title: 'Server-side verification',
    body: 'The token is checked by the control plane before a PTY is forked. A rejected handshake is closed with policy-violation code 1008.',
  },
  {
    icon: Fingerprint,
    title: 'Full session attribution',
    body: 'Every keystroke crosses an authenticated WebSocket and is recorded against the operator session for audit.',
  },
  {
    icon: Lock,
    title: 'Administrative privilege',
    body: 'Elevation grants root-level command execution on the NOC control node. Terminate the session the moment you are done.',
  },
]

interface ElevationGateProps {
  onElevate: (token: string, remember: boolean) => void
  initialToken?: string
  /** Reason the previous elevation attempt failed, e.g. a 1008 close. */
  notice?: string | null
}

/**
 * Admin-mode gate shown before the command pane activates.
 *
 * Collects the operator's terminal access token (the same shared secret the
 * FastAPI terminal endpoint validates) and hands it to the parent, which opens
 * the WebSocket. No verification happens here — a bad token surfaces as a 1008
 * denial from the control plane, which is the only authority on the matter.
 */
export function ElevationGate({ onElevate, initialToken = '', notice }: ElevationGateProps) {
  const [token, setToken] = useState(initialToken)
  const [reveal, setReveal] = useState(false)
  const [remember, setRemember] = useState(true)
  const inputRef = useRef<HTMLInputElement>(null)
  const tokenId = useId()
  const rememberId = useId()

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const canElevate = token.trim().length > 0
  const maskedToken = token ? `${'•'.repeat(Math.min(token.length, 18))}` : '••••••••••••'

  return (
    <motion.div
      initial={{ opacity: 0, y: 18, filter: 'blur(6px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, y: -14, filter: 'blur(8px)' }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      className="noc-pane noc-rim relative overflow-hidden rounded-2xl border border-emerald-500/15 bg-[#05070a]"
    >
      <div aria-hidden className="noc-grid-bg pointer-events-none absolute inset-0" />
      <div aria-hidden className="noc-shafts pointer-events-none absolute inset-0" />
      <div
        aria-hidden
        className="pointer-events-none absolute -top-24 left-[18%] h-64 w-64 rounded-full bg-emerald-500/20 blur-[110px]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-24 right-[10%] h-64 w-64 rounded-full bg-sky-500/10 blur-[110px]"
      />

      <div className="relative z-10 grid gap-10 p-6 md:p-9 lg:grid-cols-[1.05fr_0.95fr] lg:gap-12">
        {/* ---------------- left: briefing ---------------- */}
        <div className="flex flex-col">
          <div className="flex items-center gap-3 font-mono text-[10px] uppercase tracking-[0.34em] text-emerald-400/80">
            <span className="relative flex size-2 items-center justify-center">
              <span className="noc-ring absolute inset-0 rounded-full" />
              <span className="size-1.5 rounded-full bg-emerald-400" />
            </span>
            restricted surface
          </div>

          <div className="mt-6 flex items-center gap-4">
            <div className="noc-ring relative flex size-14 shrink-0 items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10 text-emerald-300">
              <ShieldCheck className="size-6" strokeWidth={1.6} />
            </div>
            <div>
              <h2 className="text-2xl font-semibold tracking-tight text-zinc-50 md:text-[28px]">
                Privileged Shell Access
              </h2>
              <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.22em] text-zinc-500">
                elevation · admin or higher
              </p>
            </div>
          </div>

          <p className="mt-6 max-w-lg text-sm leading-relaxed text-zinc-400">
            This pane bridges a live pseudo-terminal on the NOC control plane. Keystrokes stream
            upstream over an authenticated WebSocket while raw PTY bytes render back into a full
            terminal emulator — with window-size negotiation, so full-screen tools behave correctly.
          </p>

          <ul className="mt-8 space-y-4">
            {POLICY.map((item, index) => (
              <motion.li
                key={item.title}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.12 + index * 0.08, duration: 0.4 }}
                className="flex gap-3.5"
              >
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900/70 text-emerald-400/90">
                  <item.icon className="size-4" strokeWidth={1.7} />
                </div>
                <div>
                  <div className="text-sm font-medium text-zinc-200">{item.title}</div>
                  <p className="mt-0.5 text-xs leading-relaxed text-zinc-500">{item.body}</p>
                </div>
              </motion.li>
            ))}
          </ul>
        </div>

        {/* ---------------- right: elevation console ---------------- */}
        <div className="relative">
          <div className="relative overflow-hidden rounded-xl border border-zinc-800/90 bg-zinc-950/70 noc-glow-ring backdrop-blur-sm">
            <div className="flex items-center justify-between border-b border-zinc-800/80 px-4 py-2.5">
              <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.28em] text-zinc-500">
                <TerminalIcon className="size-3.5 text-emerald-400/80" />
                elevation console
              </div>
              <div className="flex items-center gap-1.5">
                <span className="size-1.5 rounded-full bg-zinc-700" />
                <span className="size-1.5 rounded-full bg-zinc-700" />
                <span className="size-1.5 rounded-full bg-emerald-500/70" />
              </div>
            </div>

            <div className="space-y-5 p-4 md:p-5">
              <div className="rounded-lg border border-zinc-800/80 bg-black/50 px-3 py-2 font-mono text-[11px] leading-relaxed">
                <div className="flex items-center gap-2 text-zinc-500">
                  <span className="text-emerald-400/80">WS</span>
                  <span className="truncate text-zinc-400">
                    /api/terminal/ws?token=<span className="text-emerald-300/80">{maskedToken}</span>
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-zinc-600">
                  <span className="text-sky-400/70">PROTO</span>
                  <span>input · resize · pty-bytes</span>
                </div>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor={tokenId}
                  className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.26em] text-zinc-500"
                >
                  <KeyRound className="size-3" />
                  operator access token
                </label>
                <div className="relative">
                  <input
                    id={tokenId}
                    ref={inputRef}
                    type={reveal ? 'text' : 'password'}
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && canElevate) onElevate(token.trim(), remember)
                    }}
                    autoComplete="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    placeholder="paste control-plane token"
                    className={cn(
                      'h-10 w-full rounded-lg border border-zinc-800 bg-black/60 pr-10 pl-3 font-mono text-sm text-emerald-200 caret-emerald-400',
                      'placeholder:font-sans placeholder:text-xs placeholder:text-zinc-600',
                      'transition-colors outline-none focus-visible:border-emerald-500/60 focus-visible:ring-2 focus-visible:ring-emerald-500/20'
                    )}
                  />
                  <button
                    type="button"
                    onClick={() => setReveal((value) => !value)}
                    aria-label={reveal ? 'Hide token' : 'Show token'}
                    className="absolute top-1/2 right-2 -translate-y-1/2 rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800/70 hover:text-zinc-300"
                  >
                    {reveal ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </button>
                </div>
                <p className="text-[11px] leading-relaxed text-zinc-600">
                  Issued from <span className="font-mono text-zinc-500">NOC_ADMIN_TOKEN</span> on
                  the control plane (the legacy <span className="font-mono text-zinc-500">TERMINAL_ACCESS_TOKEN</span>{' '}
                  alias still works). Nothing is sent until you elevate.
                </p>
              </div>

              <label
                htmlFor={rememberId}
                className="flex cursor-pointer items-center gap-2.5 text-xs text-zinc-500 transition-colors hover:text-zinc-400"
              >
                <input
                  id={rememberId}
                  type="checkbox"
                  checked={remember}
                  onChange={(event) => setRemember(event.target.checked)}
                  className="size-3.5 accent-emerald-500"
                />
                Keep the token for this browser tab
              </label>

              <AnimatePresence initial={false}>
                {notice ? (
                  <motion.div
                    key="notice"
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
                      <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />
                      <span>{notice}</span>
                    </div>
                  </motion.div>
                ) : null}
              </AnimatePresence>

              <Button
                type="button"
                size="lg"
                disabled={!canElevate}
                onClick={() => canElevate && onElevate(token.trim(), remember)}
                className={cn(
                  'group relative h-10 w-full overflow-hidden text-sm font-semibold tracking-wide',
                  'bg-emerald-500 text-emerald-950',
                  'hover:bg-emerald-400 disabled:bg-zinc-800 disabled:text-zinc-500',
                  canElevate && 'shadow-[0_0_28px_-6px_rgba(52,211,153,0.65)]'
                )}
              >
                <Lock className="size-4" strokeWidth={2.2} />
                Elevate &amp; connect
                <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" />
              </Button>
            </div>
          </div>

          <p className="mt-3 px-1 text-[10px] leading-relaxed text-zinc-600">
            Unauthorized access to this surface is prohibited and logged. Attempting elevation with an
            invalid token closes the session with code&nbsp;1008.
          </p>
        </div>
      </div>
    </motion.div>
  )
}
