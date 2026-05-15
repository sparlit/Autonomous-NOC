'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import TopologyMap from "@/components/charts/TopologyMap";

export default function TopologyPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold text-zinc-100">Network Topology</h1>
      </div>

      <Card className="bg-zinc-900/50 border-zinc-800">
        <CardHeader>
          <CardTitle>Physical & Logical Infrastructure</CardTitle>
          <CardDescription>
            Live view of interconnected devices and their operational status.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <TopologyMap />
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card className="bg-zinc-900/50 border-zinc-800">
          <CardHeader>
            <CardTitle className="text-sm">Device Health</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
                <span className="text-emerald-500 font-bold">3 Online</span>
                <span className="text-amber-500 font-bold">1 Warning</span>
                <span className="text-rose-500 font-bold">0 Offline</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
