'use client'

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";

interface Device {
  id: string;
  label: string;
  type: string;
  status: string;
  ip?: string;
  vendor?: string;
}

export default function InventoryPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'nanoc-secret-key';

  useEffect(() => {
    const fetchInventory = async () => {
      try {
        const res = await fetch(`${API_URL}/api/data/inventory`, {
          headers: { 'X-API-Key': API_KEY }
        });
        if (res.ok) {
          setDevices(await res.json());
        }
      } catch (e) {
        console.error("Failed to fetch inventory", e);
      }
    };
    fetchInventory();
  }, [API_URL, API_KEY]);

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-zinc-100">Device Inventory</h1>
      <Card className="bg-zinc-900/50 border-zinc-800">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="border-zinc-800">
                <TableHead>Hostname</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Vendor</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.map((device) => (
                <TableRow key={device.id} className="border-zinc-800 hover:bg-zinc-800/30">
                  <TableCell className="font-mono text-sm font-bold">{device.id}</TableCell>
                  <TableCell className="text-sm">{device.type || device.label}</TableCell>
                  <TableCell className="text-sm text-zinc-400">{device.ip || 'DHCP'}</TableCell>
                  <TableCell className="text-sm text-zinc-400">{device.vendor || 'Generic'}</TableCell>
                  <TableCell className="text-right">
                    <Badge className={device.status === 'online' ? 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20' : 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/20'}>
                      {device.status}
                    </Badge>
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
