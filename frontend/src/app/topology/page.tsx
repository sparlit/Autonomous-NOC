'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import TopologyMap from "@/components/charts/TopologyMap";
import { useQuery } from '@tanstack/react-query';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

const fetchTopology = async () => {
  const res = await fetch(`${API_URL}/api/data/topology`, {
    headers: { 'X-API-Key': API_KEY }
  });
  if (!res.ok) throw new Error('Failed to fetch topology');
  return res.json();
};

export default function TopologyPage() {
  const { data: topology } = useQuery({
    queryKey: ['topology'],
    queryFn: fetchTopology,
    refetchInterval: 10000,
  });

  const counts = {
    online: topology?.nodes?.filter((n: any) => n.status === 'online').length || 0,
    warning: topology?.nodes?.filter((n: any) => n.status === 'warning').length || 0,
    offline: topology?.nodes?.filter((n: any) => n.status === 'offline').length || 0,
  };

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
                <span className="text-emerald-500 font-bold">{counts.online} Online</span>
                <span className="text-amber-500 font-bold">{counts.warning} Warning</span>
                <span className="text-rose-500 font-bold">{counts.offline} Offline</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
