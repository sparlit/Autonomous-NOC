'use client'

import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'
import type { LiveState } from '@/hooks/use-live-data'

/**
 * Status chip for the live stream.
 *
 * Deliberately mirrors the terminal pane's connection chip (same border, dot
 * and mono-uppercase treatment) so every surface in the NOC speaks the same
 * visual language about connectivity.
 */
const STATE_META: Record<LiveState, { label: string; tone: string; dot: string; pulse: boolean }> = {
  connecting: {
    label: 'linking',
    tone: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    dot: 'bg-amber-400',
    pulse: true,
  },
  live: {
    label: 'live',
    tone: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    dot: 'bg-emerald-400',
    pulse: true,
  },
  reconnecting: {
    label: 'reconnecting',
    tone: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    dot: 'bg-amber-400',
    pulse: true,
  },
  down: {
    label: 'rest fallback',
    tone: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
    dot: 'bg-rose-400',
    pulse: false,
  },
}

/** Human wording for the tooltip on each state. */
const STATE_HINT: Record<LiveState, string> = {
  connecting: 'Opening the websocket stream…',
  live: 'Streaming live telemetry over websocket.',
  reconnecting: 'Stream interrupted — retrying with exponential backoff.',
  down: 'Stream unavailable — polling REST until it returns.',
}

function useSecondsSince(timestamp: number | null) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (timestamp === null) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [timestamp])

  // Derived at render time, so a new timestamp is reflected immediately
  // instead of waiting for the next tick.
  if (timestamp === null) return null
  return Math.max(0, Math.round((now - timestamp) / 1000))
}

export interface LiveStateChipProps {
  state: LiveState
  /** Epoch ms of the last frame, used for the freshness counter. */
  lastMessageAt?: number | null
  /** Retry attempt count, shown while reconnecting. */
  attempts?: number
  /** Prefix such as "telemetry" to make the chip self-describing. */
  label?: string
  className?: string
}

export function LiveStateChip({
  state,
  lastMessageAt = null,
  attempts = 0,
  label,
  className,
}: LiveStateChipProps) {
  const meta = STATE_META[state]
  const age = useSecondsSince(lastMessageAt)

  const detail =
    state === 'live' && age !== null
      ? `${age}s ago`
      : state === 'reconnecting' || state === 'down'
        ? `retry ${attempts}`
        : null

  return (
    <span
      title={STATE_HINT[state]}
      className={cn(
        'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em]',
        meta.tone,
        className
      )}
    >
      <span className="relative flex size-1.5 items-center justify-center">
        <span
          className={cn(
            'size-1.5 rounded-full',
            meta.dot,
            meta.pulse && 'animate-pulse'
          )}
        />
      </span>
      {label ? <span className="text-current/70">{label}</span> : null}
      <span>{meta.label}</span>
      {detail ? <span className="opacity-60">· {detail}</span> : null}
    </span>
  )
}
