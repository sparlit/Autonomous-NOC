'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, AlertCircle, Server, Globe } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

const fetchStatus = async () => {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/monitoring/status`);
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
};

const fetchAlertsSummary = async () => {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/alerts/summary`);
  if (!res.ok) throw new Error("Failed to fetch alerts summary");
  return res.json();
};

interface Event {
  time: string;
  event: string;
  status: string;
}

export default function Home() {
  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    refetchInterval: 5000,
  });

  const { data: alerts, isLoading: alertsLoading } = useQuery({
    queryKey: ["alertsSummary"],
    queryFn: fetchAlertsSummary,
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Network Latency</CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">
              {statusLoading ? "..." : status?.latency}
            </div>
            <p className="text-xs text-muted-foreground">
              -2ms from last hour
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
              {alertsLoading ? "..." : alerts?.active_alerts}
            </div>
            <p className="text-xs text-muted-foreground">
              {alertsLoading ? "..." : `${alerts?.urgent} urgent, ${alerts?.warning} warning`}
            </p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Server Uptime</CardTitle>
            <Server className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">
              {statusLoading ? "..." : status?.uptime}
            </div>
            <p className="text-xs text-muted-foreground">
              All systems operational
            </p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Global Traffic</CardTitle>
            <Globe className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-blue-500">
              {statusLoading ? "..." : status?.traffic}
            </div>
            <p className="text-xs text-muted-foreground">
              +15% from last hour
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
        <Card className="col-span-4 bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Network Overview</CardTitle>
            <CardDescription>
              Real-time network performance across all regions.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-[300px] w-full flex items-center justify-center border-2 border-dashed border-zinc-800 rounded-lg">
               <span className="text-zinc-500">Metrics Chart Placeholder (ECharts integration coming next)</span>
            </div>
          </CardContent>
        </Card>
        <Card className="col-span-3 bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle>Recent Events</CardTitle>
            <CardDescription>
              Latest automation actions and system logs.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {alertsLoading ? (
                <div className="text-sm text-zinc-500">Loading events...</div>
              ) : (
                alerts?.recent_events?.map((item: Event, i: number) => (
                  <div key={i} className="flex items-center text-sm">
                    <div className="w-16 text-zinc-500 font-mono text-xs">{item.time}</div>
                    <div className="flex-1 px-2">{item.event}</div>
                    <div className={`font-medium ${item.status === 'Resolved' || item.status === 'Success' ? 'text-emerald-500' : 'text-amber-500'}`}>
                      {item.status}
                    </div>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
