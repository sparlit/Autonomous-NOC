'use client'

import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, AlertCircle, Radio, Server, Clock } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import NetworkLatencyChart from '@/components/charts/NetworkLatencyChart'
import ThroughputChart from '@/components/charts/ThroughputChart'
import DeviceStatusChart from '@/components/charts/DeviceStatusChart'
import { LiveStateChip } from '@/components/live/live-state-chip'
import { API_URL } from '@/hooks/use-live-data'
import { useLiveTelemetry, type SeriesSeed } from '@/hooks/use-live-telemetry'

const fetchStatus = async () => {
  const res = await fetch(`${API_URL}/api/monitoring/status`)
  if (!res.ok) throw new Error('Failed to fetch status')
  return res.json()
}

const fetchAlertsSummary = async () => {
  const res = await fetch(`${API_URL}/api/alerts/summary`)
  if (!res.ok) throw new Error('Failed to fetch alerts summary')
  return res.json()
}

const fetchSeries = async (): Promise<SeriesSeed> => {
  const res = await fetch(`${API_URL}/api/monitoring/series?limit=30`)
  if (!res.ok) throw new Error('Failed to fetch metric series')
  return res.json()
}

interface Event {
  time: string
  event: string
  status: string
}

interface StatusBody {
  latency?: string
  uptime?: string
  traffic?: string
  status?: string
  backlog?: number
}

interface AlertsSummary {
  active_alerts?: number
  urgent?: number
  warning?: number
  recent_events?: Event[]
}

/**
 * Renders the monitoring dashboard for the Home page.
 *
 * REST paints the first frame (status, alert summary, chart history); from then
 * on every number on the page is driven by the websocket stream. Polling only
 * resumes when the socket drops — `telemetry.pollInterval()` returns the REST
 * interval for every state except `live`.
 *
 * @returns The Home page React element representing the monitoring dashboard
 */
export default function Home() {
  const seriesQuery = useQuery({ queryKey: ['metricSeries'], queryFn: fetchSeries, staleTime: 15_000 })
  const telemetry = useLiveTelemetry(seriesQuery.data)

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['status'],
    queryFn: fetchStatus,
    refetchInterval: telemetry.pollInterval(5_000),
  })

  const { data: alerts, isLoading: alertsLoading } = useQuery({
    queryKey: ['alertsSummary'],
    queryFn: fetchAlertsSummary,
    refetchInterval: telemetry.pollInterval(5_000),
  })

  const snapshot = telemetry.snapshot
  const statusBody = status as StatusBody | undefined
  const alertsBody = alerts as AlertsSummary | undefined

  // Live values win over the REST paint; REST stays as the initial frame.
  const latency = snapshot?.latency ?? statusBody?.latency
  const uptime = snapshot?.uptime ?? statusBody?.uptime
  const backlog = snapshot?.backlog ?? statusBody?.backlog
  const activeAlerts = telemetry.activeAlertCount ?? alertsBody?.active_alerts
  const urgent = telemetry.urgentCount ?? alertsBody?.urgent
  const warning = telemetry.warningCount ?? alertsBody?.warning

  const recentEvents = useMemo<Event[]>(() => {
    const live: Event[] = telemetry.alerts.map((alert) => ({
      time: (alert.timestamp ?? '').slice(11, 19) || '--:--:--',
      event: alert.title,
      status: alert.action === 'resolved' ? 'Resolved' : alert.severity === 'critical' ? 'Investigating' : 'Warning',
    }))
    const seeded = alertsBody?.recent_events ?? []
    return [...live, ...seeded].slice(0, 6)
  }, [telemetry.alerts, alertsBody])

  const latencySeries = telemetry.latencySeries.length > 0 ? telemetry.latencySeries : undefined
  const throughputSeries = telemetry.throughputSeries.length > 0 ? telemetry.throughputSeries : undefined
  const deviceSeries = telemetry.deviceSeries.length > 0 ? telemetry.deviceSeries : undefined

  const throughputLabel =
    snapshot?.throughput_in_mbps !== null && snapshot?.throughput_in_mbps !== undefined
      ? `${snapshot.throughput_in_mbps.toFixed(2)} / ${(snapshot.throughput_out_mbps ?? 0).toFixed(2)} Mbps`
      : snapshot?.traffic

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5 font-mono text-[10px] uppercase tracking-[0.34em] text-emerald-400/80">
            <Radio className="size-3" />
            streamed over websocket
          </div>
          <h1 className="mt-2 text-3xl font-bold tracking-tight text-zinc-100">Executive Overview</h1>
        </div>
        <div className="flex items-center gap-2">
          <LiveStateChip
            state={telemetry.state}
            lastMessageAt={telemetry.lastMessageAt}
            attempts={telemetry.attempts}
            label="telemetry"
          />
          <span className="hidden items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-400 sm:inline-flex">
            {throughputLabel ?? 'throughput n/a'}
          </span>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Network Latency</CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">
              {statusLoading && !latency ? '...' : (latency ?? '—')}
            </div>
            <p className="text-xs text-muted-foreground">
              {snapshot?.prometheus_ok === false ? 'Prometheus unreachable — fallback value' : 'avg(icmp_latency_ms)'}
            </p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Alerts</CardTitle>
            <AlertCircle className="h-4 w-4 text-amber-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-amber-500">
              {alertsLoading && activeAlerts === undefined ? '...' : (activeAlerts ?? '—')}
            </div>
            <p className="text-xs text-muted-foreground">
              {urgent === null || urgent === undefined
                ? 'tracking rule transitions live'
                : `${urgent} urgent, ${warning ?? 0} warning`}
            </p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Server Uptime</CardTitle>
            <Server className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">{statusLoading && !uptime ? '...' : (uptime ?? '—')}</div>
            <p className="text-xs text-muted-foreground">All systems operational</p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Task Backlog</CardTitle>
            <Clock className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-500">{statusLoading && backlog === undefined ? '...' : (backlog ?? '—')}</div>
            <p className="text-xs text-muted-foreground">Pending autonomous actions</p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <Card className="col-span-4 bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Network Latency</CardTitle>
            <CardDescription>Rolling ICMP response times, appended on every metric frame.</CardDescription>
          </CardHeader>
          <CardContent>
            <NetworkLatencyChart data={latencySeries} />
          </CardContent>
        </Card>
        <Card className="col-span-3 bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Recent Events</CardTitle>
            <CardDescription>Live alert transitions, newest first.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {alertsLoading && recentEvents.length === 0 ? (
                <div className="text-sm text-zinc-500">Loading events...</div>
              ) : recentEvents.length === 0 ? (
                <div className="text-sm text-zinc-500">No alert activity — all rules inside threshold.</div>
              ) : (
                recentEvents.map((item, i) => (
                  <div key={`${item.time}-${item.event}-${i}`} className="flex items-center text-sm">
                    <div className="w-16 shrink-0 font-mono text-xs text-zinc-500">{item.time}</div>
                    <div className="flex-1 truncate px-2">{item.event}</div>
                    <div
                      className={`font-medium ${
                        item.status === 'Resolved' || item.status === 'Success'
                          ? 'text-emerald-500'
                          : item.status === 'Warning'
                            ? 'text-amber-400'
                            : 'text-amber-500'
                      }`}
                    >
                      {item.status}
                    </div>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-2">
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Throughput</CardTitle>
            <CardDescription>Host NIC rates sampled per metric frame.</CardDescription>
          </CardHeader>
          <CardContent>
            <ThroughputChart data={throughputSeries} />
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Device Status</CardTitle>
            <CardDescription>Inventory health from the live topology.</CardDescription>
          </CardHeader>
          <CardContent>
            <DeviceStatusChart data={deviceSeries} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
