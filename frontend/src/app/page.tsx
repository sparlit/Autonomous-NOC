
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, AlertCircle, Server, Globe } from "lucide-react";

export default function Home() {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Network Latency</CardTitle>
            <Activity className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">12ms</div>
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
            <div className="text-2xl font-bold text-amber-500">3</div>
            <p className="text-xs text-muted-foreground">
              2 urgent, 1 warning
            </p>
          </CardContent>
        </Card>
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Server Uptime</CardTitle>
            <Server className="h-4 w-4 text-emerald-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-emerald-500">99.99%</div>
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
            <div className="text-2xl font-bold text-blue-500">1.2 Gbps</div>
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
              {[
                { time: "14:23:01", event: "Switch-04 port Gi0/1 bounce", status: "Resolved", color: "text-emerald-500" },
                { time: "14:20:15", event: "High latency detected in US-EAST", status: "Investigating", color: "text-amber-500" },
                { time: "14:15:30", event: "Backup job completed", status: "Success", color: "text-blue-500" },
                { time: "14:02:11", event: "Server-09 CPU spike", status: "Mitigated", color: "text-emerald-500" },
              ].map((item, i) => (
                <div key={i} className="flex items-center text-sm">
                  <div className="w-16 text-zinc-500 font-mono text-xs">{item.time}</div>
                  <div className="flex-1 px-2">{item.event}</div>
                  <div className={`font-medium ${item.color}`}>{item.status}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
