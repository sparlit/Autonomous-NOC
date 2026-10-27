'use client'

import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { LiveStateChip } from "@/components/live/live-state-chip";
import { useLiveFeed, API_URL } from "@/hooks/use-live-data";
import { CheckCircle2, Clock, AlertCircle } from "lucide-react";

/**
 * Renders the task board.
 *
 * The task list is fetched over REST for the first paint and then kept current
 * by the websocket `data_update` event: the backend fingerprints the tasks
 * table every few seconds and only emits when a row actually moved, so the
 * board re-fetches on change instead of on a timer. Polling returns only when
 * the stream drops.
 *
 * @returns A React element representing the task board UI.
 */
interface Task {
  id: string;
  status: string;
  project_id: string;
  description: string;
  assigned_to: string;
  updated_at: string;
}

const fetchTasks = async (): Promise<Task[]> => {
  const res = await fetch(`${API_URL}/api/data/tasks`);
  if (!res.ok) throw new Error('Failed to fetch tasks');
  return res.json();
};

export default function TaskBoard() {
  const queryClient = useQueryClient();
  const live = useLiveFeed({ types: ['data_update'] });

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    refetchInterval: live.pollInterval(5000),
  });

  useEffect(() => {
    if (!live.latest) return;
    const kinds = live.latest.kinds;
    if (Array.isArray(kinds) && !kinds.includes('tasks')) return;
    queryClient.invalidateQueries({ queryKey: ['tasks'] });
  }, [live.latest, queryClient]);

  const getStatusIcon = (status: string) => {
    switch (status) {
        case 'completed': return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
        case 'pending': return <Clock className="h-4 w-4 text-amber-500 animate-pulse" />;
        case 'failed': return <AlertCircle className="h-4 w-4 text-rose-500" />;
        default: return <Clock className="h-4 w-4 text-zinc-500" />;
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-3xl font-bold text-zinc-100">Task Board</h1>
        <LiveStateChip
          state={live.state}
          lastMessageAt={live.lastMessageAt}
          attempts={live.attempts}
          label="updates"
        />
      </div>

      <Card className="bg-zinc-900/50 border-zinc-800">
        <CardHeader>
          <CardTitle>Autonomous Backlog</CardTitle>
          <CardDescription>Refreshed by websocket data_update events as tasks change state.</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800">
                <TableHead className="w-[100px]">Status</TableHead>
                <TableHead>Project</TableHead>
                <TableHead>Task</TableHead>
                <TableHead>Assigned To</TableHead>
                <TableHead className="text-right">Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tasks.length === 0 && !isLoading ? (
                <TableRow className="border-zinc-800">
                  <TableCell colSpan={5} className="text-sm text-zinc-500">
                    No tasks recorded yet.
                  </TableCell>
                </TableRow>
              ) : (
                tasks.map((task) => (
                  <TableRow key={task.id} className="border-zinc-800 hover:bg-zinc-800/30">
                    <TableCell>
                      <div className="flex items-center gap-2">
                          {getStatusIcon(task.status)}
                          <span className="capitalize text-xs">{task.status}</span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{task.project_id || 'Global'}</TableCell>
                    <TableCell className="max-w-md truncate text-sm">{task.description}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="bg-zinc-950">{task.assigned_to}</Badge>
                    </TableCell>
                    <TableCell className="text-right text-xs text-zinc-500">
                      {task.updated_at ? new Date(task.updated_at).toLocaleTimeString() : '—'}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
