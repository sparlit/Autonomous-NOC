'use client'

/**
 * Dashboard-shaped view over the shared live socket.
 *
 * `useLiveFeed` is deliberately generic; this hook turns its message buffer into
 * the typed things the Executive Overview actually renders: the latest metric
 * snapshot, rolling chart series, and the alert lifecycle. Everything is derived
 * from the message buffer, so there is no second source of truth to keep in
 * sync.
 */

import { useMemo } from 'react'

import { useLiveFeed, type LiveMessage, type LiveState } from '@/hooks/use-live-data'

/** Device status histogram, as produced by the metric snapshot. */
export interface DeviceCounts {
  operational: number
  warning: number
  critical: number
  maintenance: number
}

/** One `metric` frame from the stream. */
export interface MetricSnapshot {
  latency: string
  uptime: string
  traffic: string
  status: string
  backlog: number
  latency_ms: number | null
  throughput_in_mbps: number | null
  throughput_out_mbps: number | null
  devices: DeviceCounts
  prometheus_ok: boolean
  timestamp: string
}

/** One `alert` frame from the stream. */
export interface LiveAlert {
  id: string
  title: string
  description?: string
  severity: string
  action: 'created' | 'resolved'
  timestamp?: string
}

export interface SeriesPoint {
  time: string
  value: number
}

export interface ThroughputPoint {
  time: string
  inbound: number
  outbound: number
}

/** Payload of `GET /api/monitoring/series`, used to seed the charts. */
export interface SeriesSeed {
  latency?: SeriesPoint[]
  throughput?: ThroughputPoint[]
  backlog?: SeriesPoint[]
  devices?: { name: string; value: number }[]
}

export interface LiveTelemetry {
  state: LiveState
  lastMessageAt: number | null
  attempts: number
  snapshot: MetricSnapshot | null
  alerts: LiveAlert[]
  activeAlertCount: number | null
  urgentCount: number | null
  warningCount: number | null
  latencySeries: SeriesPoint[]
  throughputSeries: ThroughputPoint[]
  backlogSeries: SeriesPoint[]
  deviceSeries: { name: string; value: number }[]
  /** Data sets the backend reported as changed, most recent first. */
  dataUpdates: string[]
  pollInterval: (intervalMs?: number) => number | false
}

/** How many points to keep per chart. */
const MAX_SERIES_POINTS = 30

function timeLabel(timestamp: string | null | undefined): string {
  if (!timestamp) return ''
  // Backend timestamps are local ISO strings ("2026-04-20T12:00:00"); fall back
  // to the raw value rather than emitting "Invalid Date" on the x-axis.
  const parsed = timestamp.includes('T') ? timestamp.slice(11, 19) : timestamp.slice(-8)
  return parsed || timestamp
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function toSnapshot(message: LiveMessage): MetricSnapshot | null {
  const devices = (message.devices ?? {}) as Partial<DeviceCounts>
  const status = typeof message.status === 'string' ? message.status : 'unknown'
  if (typeof message.latency !== 'string') return null
  return {
    latency: message.latency,
    uptime: typeof message.uptime === 'string' ? message.uptime : '—',
    traffic: typeof message.traffic === 'string' ? message.traffic : '—',
    status,
    backlog: asNumber(message.backlog) ?? 0,
    latency_ms: asNumber(message.latency_ms),
    throughput_in_mbps: asNumber(message.throughput_in_mbps),
    throughput_out_mbps: asNumber(message.throughput_out_mbps),
    devices: {
      operational: asNumber(devices.operational) ?? 0,
      warning: asNumber(devices.warning) ?? 0,
      critical: asNumber(devices.critical) ?? 0,
      maintenance: asNumber(devices.maintenance) ?? 0,
    },
    prometheus_ok: message.prometheus_ok === true,
    timestamp: typeof message.timestamp === 'string' ? message.timestamp : '',
  }
}

function toAlert(message: LiveMessage): LiveAlert | null {
  const id = typeof message.id === 'string' ? message.id : null
  if (!id) return null
  return {
    id,
    title: typeof message.title === 'string' ? message.title : id,
    description: typeof message.description === 'string' ? message.description : undefined,
    severity: typeof message.severity === 'string' ? message.severity : 'warning',
    action: message.action === 'resolved' ? 'resolved' : 'created',
    timestamp: typeof message.timestamp === 'string' ? message.timestamp : undefined,
  }
}

/** Append fresh points to a seed series, dropping duplicate timestamps. */
function mergeSeries<T extends { time: string }>(
  seed: T[] | undefined,
  live: T[],
  limit = MAX_SERIES_POINTS
): T[] {
  const merged: T[] = []
  const seen = new Set<string>()
  for (const point of [...(seed ?? []), ...live]) {
    if (!point.time || seen.has(point.time)) continue
    seen.add(point.time)
    merged.push(point)
  }
  return merged.slice(-limit)
}

/**
 * Subscribe to the live stream and derive the dashboard's view of it.
 *
 * @param seed Chart series fetched over REST so the first paint is real instead
 *   of empty while the buffer fills.
 * @param enabled Set `false` to keep the socket closed.
 */
export function useLiveTelemetry(seed?: SeriesSeed, enabled = true): LiveTelemetry {
  const feed = useLiveFeed({ bufferSize: 90, enabled })

  return useMemo<LiveTelemetry>(() => {
    // The hook buffers newest-first; walk it the other way for anything
    // order-dependent (alert counts, chart series).
    const chronological = [...feed.messages].reverse()

    let snapshot: MetricSnapshot | null = null
    let seedSnapshot: MetricSnapshot | null = null
    const alerts: LiveAlert[] = []
    const latencyPoints: SeriesPoint[] = []
    const throughputPoints: ThroughputPoint[] = []
    const backlogPoints: SeriesPoint[] = []
    const dataUpdates: string[] = []

    let baseActive: number | null = null
    let baseUrgent: number | null = null
    let baseWarning: number | null = null

    for (const message of chronological) {
      if (message.type === 'hello') {
        const helloSnapshot = message.snapshot
        if (helloSnapshot && typeof helloSnapshot === 'object') {
          const parsed = toSnapshot(helloSnapshot as LiveMessage)
          if (parsed) seedSnapshot = parsed
        }
        baseActive = asNumber(message.active_alerts) ?? baseActive
        continue
      }

      if (message.type === 'metric') {
        const parsed = toSnapshot(message)
        if (!parsed) continue
        snapshot = parsed
        const label = timeLabel(parsed.timestamp)
        if (parsed.latency_ms !== null) {
          latencyPoints.push({ time: label, value: parsed.latency_ms })
        }
        if (parsed.throughput_in_mbps !== null && parsed.throughput_out_mbps !== null) {
          throughputPoints.push({
            time: label,
            inbound: parsed.throughput_in_mbps,
            outbound: parsed.throughput_out_mbps,
          })
        }
        backlogPoints.push({ time: label, value: parsed.backlog })
        continue
      }

      if (message.type === 'alert') {
        const alert = toAlert(message)
        if (!alert) continue
        alerts.push(alert)
        continue
      }

      if (message.type === 'data_update') {
        const kinds = Array.isArray(message.kinds) ? message.kinds : []
        for (const kind of kinds) {
          if (typeof kind === 'string' && !dataUpdates.includes(kind)) {
            dataUpdates.push(kind)
          }
        }
      }
    }

    // Alert counts: the stream only sends transitions, so start from the count
    // the server reported on connect and walk each event forward.
    let activeAlertCount = baseActive
    let urgentCount = baseUrgent
    let warningCount = baseWarning
    for (const alert of alerts) {
      if (activeAlertCount === null) break
      const delta = alert.action === 'created' ? 1 : -1
      activeAlertCount = Math.max(0, activeAlertCount + delta)
      if (alert.severity === 'critical') {
        urgentCount = urgentCount === null ? null : Math.max(0, urgentCount + delta)
      } else if (alert.severity === 'warning') {
        warningCount = warningCount === null ? null : Math.max(0, warningCount + delta)
      }
    }

    const resolvedSnapshot = snapshot ?? seedSnapshot
    const deviceCounts = resolvedSnapshot?.devices

    return {
      state: feed.state,
      lastMessageAt: feed.lastMessageAt,
      attempts: feed.attempts,
      snapshot: resolvedSnapshot,
      alerts: [...alerts].reverse().slice(0, 12),
      activeAlertCount,
      urgentCount,
      warningCount,
      latencySeries: mergeSeries(seed?.latency, latencyPoints),
      throughputSeries: mergeSeries(seed?.throughput, throughputPoints),
      backlogSeries: mergeSeries(seed?.backlog, backlogPoints),
      deviceSeries: deviceCounts
        ? [
            { name: 'Operational', value: deviceCounts.operational },
            { name: 'Warning', value: deviceCounts.warning },
            { name: 'Critical', value: deviceCounts.critical },
            { name: 'Maintenance', value: deviceCounts.maintenance },
          ]
        : (seed?.devices ?? []),
      dataUpdates: [...dataUpdates].reverse().slice(0, 5),
      pollInterval: feed.pollInterval,
    }
  }, [feed, seed])
}
