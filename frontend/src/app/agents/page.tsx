'use client'

import { useEffect, useState, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Brain, Terminal as TerminalIcon, Cpu, Activity } from "lucide-react";

interface AgentEvent {
  _topic: string;
  agent_id: string;
  role: string;
  content?: string;
  prompt?: string;
  response?: string;
  _timestamp: string;
}

/**
 * Agent Operations page component that displays agents and two live agent event streams.
 *
 * Fetches agent metadata from the configured API URL and subscribes to a backend WebSocket to receive agent-related events. Maintains an in-memory list of the most recent 100 agent events and renders a grid of agent cards, a "Live Thinking Stream" for thought events, and an "Action Logs" view for log events.
 *
 * @returns A React element rendering the Agent Operations UI
 */
export default function AgentOperations() {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    const fetchAgents = async () => {
        const res = await fetch(`${API_URL}/api/data/agents`);
        if (res.ok) setAgents(await res.json());
    };
    fetchAgents();

    const socket = new WebSocket(`${API_URL.replace('http', 'ws')}/ws`);

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data._topic?.startsWith('agent/')) {
        setEvents((prev) => [data, ...prev].slice(0, 100));
      }
    };

    return () => socket.close();
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-zinc-100">Agent Operations</h1>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {agents.map((agent) => (
          <Card key={agent.id} className="bg-zinc-900/50 border-zinc-800">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">{agent.id}</CardTitle>
              <Cpu className="h-4 w-4 text-emerald-500" />
            </CardHeader>
            <CardContent>
              <div className="text-xl font-bold text-zinc-200">{agent.role}</div>
              <Badge variant={agent.status === 'idle' ? 'outline' : 'default'} className="mt-2 capitalize">
                {agent.status}
              </Badge>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="bg-zinc-900/50 border-zinc-800 flex flex-col h-[600px]">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5 text-purple-500" />
              Live Thinking Stream
            </CardTitle>
            <CardDescription>Real-time cognitive process of NANOC agents.</CardDescription>
          </CardHeader>
          <CardContent className="flex-1 overflow-hidden p-0">
             <ScrollArea className="h-full px-4">
                <div className="space-y-4 py-4">
                    {events.filter(e => e._topic.includes('thought')).map((event, i) => (
                        <div key={i} className="border-l-2 border-purple-500/30 pl-4 py-1">
                            <div className="flex items-center gap-2 mb-1">
                                <span className="text-xs font-mono text-zinc-500">{new Date(event._timestamp).toLocaleTimeString()}</span>
                                <Badge variant="secondary" className="text-[10px]">{event.role}</Badge>
                                <span className="text-xs font-bold text-purple-400">
                                    {event._topic === 'agent/thought/start' ? 'Thinking...' : 'Responded'}
                                </span>
                            </div>
                            <p className="text-sm text-zinc-300 italic line-clamp-3">
                                {event.prompt || event.response}
                            </p>
                        </div>
                    ))}
                </div>
             </ScrollArea>
          </CardContent>
        </Card>

        <Card className="bg-zinc-900/50 border-zinc-800 flex flex-col h-[600px]">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TerminalIcon className="h-5 w-5 text-emerald-500" />
              Action Logs
            </CardTitle>
            <CardDescription>System-level executions and tool usage.</CardDescription>
          </CardHeader>
          <CardContent className="flex-1 overflow-hidden p-0">
            <ScrollArea className="h-full px-4">
                <div className="space-y-2 py-4">
                    {events.filter(e => e._topic === 'agent/log').map((event, i) => (
                        <div key={i} className="font-mono text-xs text-zinc-400">
                             <span className="text-zinc-600">[{new Date(event._timestamp).toLocaleTimeString()}]</span>
                             <span className="text-emerald-500 ml-2">[{event.role}]</span>
                             <span className="ml-2">{event.content}</span>
                        </div>
                    ))}
                </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
