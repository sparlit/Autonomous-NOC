'use client'

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";

interface LogEntry {
  agent_id: string;
  content: string;
  timestamp: string;
}

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const res = await fetch(`${API_URL}/logs?limit=100`, {
          headers: { 'X-API-Key': API_KEY }
        });
        if (res.ok) {
          const data = await res.json();
          setLogs(data.logs || []);
        }
      } catch (e) {
        console.error("Failed to fetch logs", e);
      }
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 3000);
    return () => clearInterval(interval);
  }, [API_URL, API_KEY]);

  return (
    <div className="space-y-6 h-[calc(100vh-120px)] flex flex-col">
      <h1 className="text-3xl font-bold text-zinc-100">Action Logs</h1>
      <Card className="bg-zinc-950 border-zinc-800 flex-1 overflow-hidden flex flex-col">
        <CardHeader className="py-3 border-b border-zinc-800">
            <CardTitle className="text-xs text-zinc-500 uppercase">System Cognitive Stream</CardTitle>
        </CardHeader>
        <CardContent className="flex-1 p-0 overflow-hidden">
          <ScrollArea className="h-full p-4">
            <div className="space-y-2 font-mono text-xs">
              {logs.map((log, i) => (
                <div key={i} className="flex gap-4 border-b border-zinc-900 pb-2 last:border-0">
                  <span className="text-zinc-600 shrink-0">{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <span className="text-emerald-500 font-bold shrink-0">[{log.agent_id}]</span>
                  <span className="text-zinc-300">{log.content}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}
