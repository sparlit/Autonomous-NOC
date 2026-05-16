'use client'

import { useState, useEffect, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function TerminalPage() {
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<{ type: 'cmd' | 'out', text: string }[]>([
    { type: 'out', text: 'NANOC Autonomous Terminal v2.0' },
    { type: 'out', text: 'Type "help" for a list of available system commands.' }
  ]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [history]);

  const handleCommand = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;

    const cmd = input.trim();
    setHistory(prev => [...prev, { type: 'cmd', text: cmd }]);
    setInput('');

    // Local command processing
    if (cmd === 'help') {
        setHistory(prev => [...prev, { type: 'out', text: 'Available commands: help, clear, status, agents, whoami, version' }]);
    } else if (cmd === 'clear') {
        setHistory([]);
    } else if (cmd === 'status') {
        setHistory(prev => [...prev, { type: 'out', text: 'System: Operational | Backlog: 0 | Latency: 12ms' }]);
    } else if (cmd === 'version') {
        setHistory(prev => [...prev, { type: 'out', text: 'NANOC v2.0.0-production' }]);
    } else if (cmd === 'whoami') {
        setHistory(prev => [...prev, { type: 'out', text: 'admin@nanoc-core' }]);
    } else if (cmd === 'agents') {
        setHistory(prev => [...prev, { type: 'out', text: 'Active Agents: Governor, TeamLeader, Architect, Planner, Coder, Reviewer, Analyst, Documentation' }]);
    } else {
        setHistory(prev => [...prev, { type: 'out', text: `Command not found: ${cmd}` }]);
    }
  };

  return (
    <div className="space-y-6 h-[calc(100vh-120px)]">
      <h1 className="text-3xl font-bold text-zinc-100">System Terminal</h1>

      <Card className="bg-zinc-950 border-zinc-800 h-full flex flex-col font-mono">
        <CardHeader className="py-3 border-b border-zinc-800">
          <CardTitle className="text-xs text-zinc-500 uppercase tracking-widest flex items-center gap-2">
            <div className="size-2 rounded-full bg-emerald-500 animate-pulse" />
            Live Session: nanoc-core
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1 p-0 overflow-hidden flex flex-col">
          <ScrollArea className="flex-1 p-4" ref={scrollRef}>
            <div className="space-y-1 text-sm">
              {history.map((line, i) => (
                <div key={i} className="flex gap-2">
                  <span className={line.type === 'cmd' ? 'text-emerald-500' : 'text-zinc-400'}>
                    {line.type === 'cmd' ? '>' : ''}
                  </span>
                  <span className={line.type === 'cmd' ? 'text-zinc-100' : 'text-zinc-500 italic'}>
                    {line.text}
                  </span>
                </div>
              ))}
            </div>
          </ScrollArea>
          <div className="p-4 border-t border-zinc-800 bg-zinc-950">
            <form onSubmit={handleCommand} className="flex items-center gap-2">
              <span className="text-emerald-500 font-bold">{'>'}</span>
              <Input
                className="bg-transparent border-none text-zinc-100 focus-visible:ring-0 p-0 h-auto font-mono text-sm"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                autoFocus
                placeholder="Enter command..."
              />
            </form>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
