'use client'

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

interface Alert {
  id: string;
  source: string;
  title: string;
  description: string;
  severity: string;
  timestamp: string;
  status: string;
}

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

  useEffect(() => {
    const fetchAlerts = async () => {
      try {
        const res = await fetch(`${API_URL}/api/alerts/all`, {
          headers: { 'X-API-Key': API_KEY }
        });
        if (res.ok) {
          const data = await res.json();
          setAlerts(data.alerts || []);
        }
      } catch (e) {
        console.error("Failed to fetch alerts", e);
      }
    };
    fetchAlerts();
    const interval = setInterval(fetchAlerts, 10000);
    return () => clearInterval(interval);
  }, [API_URL, API_KEY]);

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-zinc-100">Active Incidents</h1>
      <Card className="bg-zinc-900/50 border-zinc-800">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800">
                <TableHead>Severity</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {alerts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-10 text-zinc-500 italic">
                    No active incidents detected.
                  </TableCell>
                </TableRow>
              ) : (
                alerts.map((alert) => (
                  <TableRow key={alert.id} className="border-zinc-800 hover:bg-zinc-800/30">
                    <TableCell>
                      <Badge variant={alert.severity === 'critical' ? 'destructive' : 'outline'} className="uppercase text-[10px]">
                        {alert.severity}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-zinc-400 font-mono">{alert.source}</TableCell>
                    <TableCell className="font-medium text-sm">{alert.title}</TableCell>
                    <TableCell className="text-xs text-zinc-500">{alert.description}</TableCell>
                    <TableCell className="text-right text-xs text-zinc-500">
                      {new Date(alert.timestamp).toLocaleString()}
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
