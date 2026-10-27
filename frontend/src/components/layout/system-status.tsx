'use client'

import { useQuery } from "@tanstack/react-query";

import { useLiveFeed, API_URL } from "@/hooks/use-live-data";

const fetchStatus = async () => {
  const res = await fetch(`${API_URL}/api/monitoring/status`);
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
};

export function SystemStatus() {
  // One shared socket for the whole app: the header is always mounted, so it
  // keeps the stream warm and reports its state on every page.
  const live = useLiveFeed({ types: ['metric', 'hello'] });

  const { data, isLoading } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    refetchInterval: live.pollInterval(5000),
  });

  if (isLoading) return <span className="text-sm font-medium text-zinc-500">LOADING...</span>;

  const isNominal = data?.status === 'nominal';

  const streamTone =
    live.state === 'live'
      ? 'text-emerald-500'
      : live.state === 'down'
        ? 'text-rose-400'
        : 'text-amber-400';

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2">
        <div className={`h-2 w-2 rounded-full ${isNominal ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`} />
        <span className={`text-sm font-medium ${isNominal ? 'text-emerald-500' : 'text-amber-500'}`}>
          SYSTEMS {data?.status?.toUpperCase() || 'UNKNOWN'}
        </span>
      </div>
      <span
        title={`Websocket stream: ${live.state}`}
        className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.2em] ${streamTone}`}
      >
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            live.state === 'live'
              ? 'bg-emerald-400 animate-pulse'
              : live.state === 'down'
                ? 'bg-rose-400'
                : 'bg-amber-400 animate-pulse'
          }`}
        />
        {live.state === 'live' ? 'LIVE' : live.state === 'down' ? 'REST' : 'LINK'}
      </span>
    </div>
  );
}
