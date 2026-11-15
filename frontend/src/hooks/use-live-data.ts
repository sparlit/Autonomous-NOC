'use client'

/**
 * Shared live-data hook for the NOC dashboard.
 *
 * One `WebSocket` per browser tab, created lazily on the first subscriber and
 * torn down when the last one unmounts — so adding another live surface costs a
 * subscriber, not another socket and another reconnect loop.
 *
 * Resilience:
 *  - exponential backoff with ±30% jitter between reconnect attempts
 *  - heartbeat (`ping`/`pong`) plus a watchdog that force-closes a socket which
 *    has gone silent, because a half-open connection never fires `onclose`
 *  - stale-event dedupe on `event_id`, with a reset when the backend's event
 *    sequence restarts (a wiped dev database would otherwise mute the stream)
 *  - `pollInterval()` hands back a REST refetch interval for every state except
 *    `live`, which is how pages degrade to polling while the socket is down
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/**
 * `sessionStorage` key the elevation gate (`/terminal`) writes the operator's
 * control-plane token to. It is the single place the browser holds a
 * credential, so the live-data socket and every REST call read it from here.
 *
 * The backend treats that token as the **admin** secret, which is a superset of
 * the read-only role the dashboard needs — so one elevation unlocks both the
 * shell and the read surfaces when the deployment sets `NOC_OPERATOR_TOKEN`.
 */
export const TOKEN_STORAGE_KEY = 'noc.terminal.operator-token'

/** Read the stored token, or `''` when the operator has not elevated. */
export function storedToken(): string {
  if (typeof window === 'undefined') return ''
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY) ?? ''
  } catch {
    // Private-mode / disabled storage: behave as "no credential".
    return ''
  }
}

/**
 * Request headers carrying the stored token, for `fetch()`.
 *
 * Empty when nothing is stored, so open (token-less) deployments are unaffected.
 * A `WebSocket` handshake cannot set headers, so the socket uses `?token=`
 * instead — see `socketUrl()`.
 */
export function authHeaders(): Record<string, string> {
  const token = storedToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** Append the stored token to a WebSocket URL (`?token=` or `&token=`). */
export function withToken(url: string): string {
  const token = storedToken()
  if (!token) return url
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

export type LiveState = 'connecting' | 'live' | 'reconnecting' | 'down'

export interface LiveMessage {
  type: string
  topic?: string | null
  ts?: string | null
  event_id?: number | null
  _topic?: string | null
  _event_id?: number | null
  _timestamp?: string | null
  [key: string]: unknown
}

/** First retry delay; doubles per attempt up to `MAX_BACKOFF_MS`. */
const BASE_BACKOFF_MS = 700
const MAX_BACKOFF_MS = 15_000
/** Attempts tolerated before the state is reported as `down` (REST fallback). */
const DOWN_AFTER_ATTEMPTS = 3
const JITTER_RATIO = 0.3
const PING_INTERVAL_MS = 20_000
/** A socket silent for this long is treated as dead and recycled. */
const STALE_AFTER_MS = 45_000
/** Backend event id going backwards by more than this means the DB restarted. */
const EVENT_ID_RESET_THRESHOLD = 1_000

type MessageSink = (message: LiveMessage) => void
type StateSink = (state: LiveState) => void

function socketUrl(): string {
  const base = API_URL.replace(/^http/, 'ws').replace(/\/$/, '')
  // `/ws` carries topology and alert state, so the backend requires at least
  // the OPERATOR role once `NOC_OPERATOR_TOKEN` is configured. Browsers cannot
  // set headers on a WebSocket handshake, hence `?token=`.
  return withToken(`${base}/ws`)
}

/** Exponential backoff with jitter, capped. */
export function backoffDelay(attempt: number): number {
  const capped = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.max(0, attempt))
  const jitter = capped * JITTER_RATIO * (Math.random() * 2 - 1)
  return Math.max(0, Math.round(capped + jitter))
}

/**
 * Singleton socket shared by every `useLiveFeed` consumer on the page.
 *
 * Kept out of React state on purpose: the connection is a resource, and each
 * change to it must fan out to subscribers rather than force a re-render of the
 * module.
 */
class LiveSocket {
  private socket: WebSocket | null = null
  private messageSinks = new Set<MessageSink>()
  private stateSinks = new Set<StateSink>()
  private attempts = 0
  private lastEventId: number | null = null
  private lastMessageAt: number | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private state: LiveState = 'connecting'

  getState(): LiveState {
    return this.state
  }

  getLastMessageAt(): number | null {
    return this.lastMessageAt
  }

  getAttempts(): number {
    return this.attempts
  }

  subscribe(onMessage: MessageSink, onState: StateSink): () => void {
    this.messageSinks.add(onMessage)
    this.stateSinks.add(onState)
    onState(this.state)

    if (!this.socket && this.messageSinks.size === 1) {
      this.connect()
    } else {
      // Late subscriber on a healthy socket: replay the current state so it
      // does not sit on "connecting" until the next transition.
      onState(this.state)
    }

    return () => {
      this.messageSinks.delete(onMessage)
      this.stateSinks.delete(onState)
      if (this.messageSinks.size === 0) {
        this.teardown()
      }
    }
  }

  private setState(next: LiveState) {
    if (this.state === next) return
    this.state = next
    this.stateSinks.forEach((sink) => sink(next))
  }

  private connect() {
    if (typeof window === 'undefined') return
    this.setState(this.attempts === 0 ? 'connecting' : 'reconnecting')

    let socket: WebSocket
    try {
      socket = new WebSocket(socketUrl())
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      this.attempts = 0
      // A fresh connection is a fresh sequence: never inherit a watermark that
      // belongs to a previous backend process.
      this.lastEventId = null
      this.lastMessageAt = Date.now()
      this.setState('live')
      this.startHeartbeat()
    }

    socket.onmessage = (event) => {
      this.lastMessageAt = Date.now()
      this.dispatch(event.data)
    }

    socket.onerror = () => {
      // `onclose` always follows; the reconnect is scheduled there so we only
      // have one place that owns the retry state machine.
    }

    socket.onclose = () => {
      this.stopHeartbeat()
      this.socket = null
      this.scheduleReconnect()
    }
  }

  private dispatch(raw: unknown) {
    if (typeof raw !== 'string') return
    let message: LiveMessage
    try {
      message = JSON.parse(raw) as LiveMessage
    } catch {
      return
    }
    if (!message || typeof message.type !== 'string') return
    if (!this.isFresh(message)) return

    this.messageSinks.forEach((sink) => {
      try {
        sink(message)
      } catch {
        /* a broken consumer must not starve the others */
      }
    })
  }

  /** Drop events we have already delivered, allowing for a restarted sequence. */
  private isFresh(message: LiveMessage): boolean {
    const id = typeof message.event_id === 'number' ? message.event_id : message._event_id
    if (typeof id !== 'number') return true

    if (this.lastEventId !== null) {
      if (id <= this.lastEventId) {
        const wentBackwards = this.lastEventId - id > EVENT_ID_RESET_THRESHOLD
        if (!wentBackwards) return false
        this.lastEventId = null
      }
    }
    this.lastEventId = id
    return true
  }

  private startHeartbeat() {
    this.stopHeartbeat()
    this.pingTimer = setInterval(() => {
      if (this.lastMessageAt !== null && Date.now() - this.lastMessageAt > STALE_AFTER_MS) {
        // Half-open socket: no events and no close frame. Recycle it.
        this.socket?.close()
        return
      }
      if (this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: 'ping', ts: new Date().toISOString() }))
      }
    }, PING_INTERVAL_MS)
  }

  private stopHeartbeat() {
    if (this.pingTimer !== null) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer !== null) return
    if (this.messageSinks.size === 0) return

    this.attempts += 1
    this.setState(this.attempts >= DOWN_AFTER_ATTEMPTS ? 'down' : 'reconnecting')

    const delay = backoffDelay(this.attempts - 1)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (this.messageSinks.size > 0) this.connect()
    }, delay)
  }

  private teardown() {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.stopHeartbeat()
    const socket = this.socket
    this.socket = null
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onerror = null
      socket.onclose = null
      try {
        socket.close()
      } catch {
        /* already closed */
      }
    }
    this.attempts = 0
    this.lastEventId = null
    this.setState('connecting')
  }
}

let sharedSocket: LiveSocket | null = null

function getSocket(): LiveSocket {
  if (!sharedSocket) sharedSocket = new LiveSocket()
  return sharedSocket
}

export interface LiveFeedOptions {
  /** Only keep messages of these `type`s. Omit for everything. */
  types?: string[]
  /** Ring-buffer size for `messages` (default 60). */
  bufferSize?: number
  /** Open the socket at all; set `false` on pages that have no live surface. */
  enabled?: boolean
}

export interface LiveFeed {
  /** Connection state for the page's status chip. */
  state: LiveState
  /** Recent messages of the requested types, newest first. */
  messages: LiveMessage[]
  /** Most recent message of the requested types. */
  latest: LiveMessage | null
  /** Epoch ms of the last frame of any type, or `null` before the first. */
  lastMessageAt: number | null
  /** Consecutive failed connection attempts. */
  attempts: number
  /** `false` while live, a refetch interval otherwise — pass straight to useQuery. */
  pollInterval: (intervalMs?: number) => number | false
}

/**
 * Subscribe to the NOC's live stream.
 *
 * @param options.types      Filter for the `messages`/`latest` buffers.
 * @param options.bufferSize How many messages to retain (default 60).
 * @param options.enabled    Set `false` to keep the socket closed.
 */
export function useLiveFeed(options: LiveFeedOptions = {}): LiveFeed {
  const { types, bufferSize = 60, enabled = true } = options
  const typeKey = useMemo(() => (types ? [...types].sort().join(',') : ''), [types])

  const [state, setState] = useState<LiveState>('connecting')
  const [messages, setMessages] = useState<LiveMessage[]>([])
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null)
  const [attempts, setAttempts] = useState(0)

  const allowed = useMemo(
    () => (typeKey ? new Set(typeKey.split(',')) : null),
    [typeKey]
  )

  useEffect(() => {
    if (!enabled) return
    const socket = getSocket()
    const unsubscribe = socket.subscribe(
      (message) => {
        if (allowed && !allowed.has(message.type)) return
        setLastMessageAt(Date.now())
        setMessages((previous) => [message, ...previous].slice(0, bufferSize))
      },
      (next) => {
        setState(next)
        setAttempts(socket.getAttempts())
        // subscribe() always sinks the current state synchronously (first
        // subscriber via connect(), later ones via the replay), so picking the
        // timestamp up here catches frames that predate this subscriber.
        setLastMessageAt(socket.getLastMessageAt())
      }
    )
    return unsubscribe
  }, [allowed, bufferSize, enabled])

  const pollInterval = useCallback(
    (intervalMs = 5_000) => (state === 'live' ? false : intervalMs),
    [state]
  )

  return {
    state: enabled ? state : 'down',
    messages,
    latest: messages[0] ?? null,
    lastMessageAt,
    attempts,
    pollInterval,
  }
}

/**
 * Build a `refetchInterval` for React Query from a live state.
 *
 * Convenience wrapper for components that do not want the full feed.
 */
export function pollingFallback(state: LiveState, intervalMs = 5_000): number | false {
  return state === 'live' ? false : intervalMs
}
