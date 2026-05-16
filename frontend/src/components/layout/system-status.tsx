'use client'

import { useQuery } from "@tanstack/react-query";

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

const fetchStatus = async () => {
  const res = await fetch(`${API_URL}/api/monitoring/status`, {
    headers: { 'X-API-Key': API_KEY }
  });
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
};

export function SystemStatus() {
  const { data, isLoading } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    refetchInterval: 5000,
  });

  if (isLoading) return <span className="text-sm font-medium text-zinc-500">LOADING...</span>;

  const isNominal = data?.status === 'nominal';

  return (
    <div className="flex items-center gap-2">
      <div className={`h-2 w-2 rounded-full ${isNominal ? 'bg-emerald-500 animate-pulse' : 'bg-amber-500'}`} />
      <span className={`text-sm font-medium ${isNominal ? 'text-emerald-500' : 'text-amber-500'}`}>
        SYSTEMS {data?.status?.toUpperCase() || 'UNKNOWN'}
      </span>
    </div>
  );
}
