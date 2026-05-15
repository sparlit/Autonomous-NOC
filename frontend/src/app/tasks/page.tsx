'use client'

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CheckCircle2, Clock, AlertCircle } from "lucide-react";

/**
 * Renders a task board that polls the tasks API every 5 seconds and displays current tasks with status, project, description, assignee, and last-updated time.
 *
 * The component reads the API base from `NEXT_PUBLIC_API_URL` (falls back to `http://localhost:8000`), fetches `/api/data/tasks` on mount and updates state only for successful HTTP responses. The polling interval is cleared on unmount.
 *
 * @returns A React element representing the task board UI.
 */
export default function TaskBoard() {
  const [tasks, setTasks] = useState<any[]>([]);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  useEffect(() => {
    const fetchTasks = async () => {
        const res = await fetch(`${API_URL}/api/data/tasks`);
        if (res.ok) setTasks(await res.json());
    };
    fetchTasks();
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, []);

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
      <h1 className="text-3xl font-bold text-zinc-100">Task Board</h1>

      <Card className="bg-zinc-900/50 border-zinc-800">
        <CardHeader>
          <CardTitle>Autonomous Backlog</CardTitle>
          <CardDescription>Real-time progress of system tasks and agent assignments.</CardDescription>
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
              {tasks.map((task) => (
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
                    {new Date(task.updated_at).toLocaleTimeString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
