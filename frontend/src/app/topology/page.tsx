'use client'

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import TopologyMap from "@/components/charts/TopologyMap";
import { LiveStateChip } from "@/components/live/live-state-chip";
import { useLiveFeed } from "@/hooks/use-live-data";
import { API_URL, authHeaders } from "@/hooks/use-live-data";

interface TopologyNode {
  id: string;
  status: string;
}

const fetchTopology = async (): Promise<{ nodes: TopologyNode[] }> => {
  const res = await fetch(`${API_URL}/api/data/topology`, { headers: authHeaders() });
  if (!res.ok) throw new Error('Failed to fetch topology');
  return res.json();
};

/**
 * Render the Network Topology page layout.
 *
 * The map itself refetches on websocket `data_update` events; this page reads
 * the same query for the device-health counters so the summary and the graph
 * can never disagree.
 *
 * @returns A React element representing the Network Topology page UI.
 */
export default function TopologyPage() {
  const live = useLiveFeed({ types: ['data_update'] });
  const { data } = useQuery({ queryKey: ['topology'], queryFn: fetchTopology });

  const nodes = data?.nodes ?? [];
  const online = nodes.filter((node) => node.status === 'online').length;
  const warning = nodes.filter((node) => node.status === 'warning').length;
  const offline = nodes.length - online - warning;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-3xl font-bold text-zinc-100">Network Topology</h1>
        <LiveStateChip
          state={live.state}
          lastMessageAt={live.lastMessageAt}
          attempts={live.attempts}
          label="changes"
        />
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
                <span className="text-emerald-500 font-bold">{online} Online</span>
                <span className="text-amber-500 font-bold">{warning} Warning</span>
                <span className="text-rose-500 font-bold">{offline} Offline</span>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
