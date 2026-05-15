'use client'

import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import { useQuery } from '@tanstack/react-query';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const fetchTopology = async () => {
  const res = await fetch(`${API_URL}/api/data/topology`);
  if (!res.ok) throw new Error('Failed to fetch topology');
  return res.json();
};

/**
 * Renders a live, force-directed network topology chart using ECharts and keeps it updated.
 *
 * Polls the topology API every 10 seconds, displays a centered "Initializing Topology..." placeholder while loading, maps nodes and edges into the graph series (node color reflects status and symbol reflects type), and attaches a window resize listener; the chart is disposed on unmount.
 *
 * @returns The React element containing the topology chart or the loading placeholder
 */
export default function TopologyMap() {
  const chartRef = useRef<HTMLDivElement>(null);
  const { data: topology, isLoading } = useQuery({
    queryKey: ['topology'],
    queryFn: fetchTopology,
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (!chartRef.current || !topology) return;

    const chart = echarts.init(chartRef.current, 'dark');

    const option = {
      title: {
        text: 'Live Network Topology',
        left: 'center',
        textStyle: { color: '#71717a', fontSize: 14 }
      },
      tooltip: {},
      animationDurationUpdate: 1500,
      animationEasingUpdate: 'quinticInOut',
      series: [
        {
          type: 'graph',
          layout: 'force',
          symbolSize: 50,
          roam: true,
          label: {
            show: true,
            position: 'bottom',
            color: '#e4e4e7'
          },
          force: {
            repulsion: 1000,
            edgeLength: 200
          },
          draggable: true,
          data: topology.nodes.map((node: any) => ({
            name: node.id,
            value: node.label,
            category: node.type,
            itemStyle: {
              color: node.status === 'online' ? '#10b981' : (node.status === 'warning' ? '#f59e0b' : '#ef4444')
            },
            symbol: node.type === 'router' ? 'diamond' : 'circle'
          })),
          links: topology.edges.map((edge: any) => ({
            source: edge.from,
            target: edge.to,
            label: {
              show: true,
              formatter: edge.label
            },
            lineStyle: {
              color: '#3f3f46',
              curveness: 0.1
            }
          })),
          lineStyle: {
            opacity: 0.9,
            width: 2,
            curveness: 0
          }
        }
      ]
    };

    chart.setOption(option);

    const handleResize = () => chart.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.dispose();
    };
  }, [topology]);

  if (isLoading) return <div className="flex items-center justify-center h-[600px] text-zinc-500">Initializing Topology...</div>;

  return (
    <div ref={chartRef} style={{ width: '100%', height: '600px' }} className="bg-zinc-950/50 rounded-xl border border-zinc-800" />
  );
}
