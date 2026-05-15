'use client'

import React from 'react';
import BaseChart from './BaseChart';
import { EChartsOption } from 'echarts';

interface ThroughputChartProps {
  data?: { time: string; inbound: number; outbound: number }[];
}

const ThroughputChart: React.FC<ThroughputChartProps> = ({ data }) => {
  const chartData = data || [
    { time: '10:00', inbound: 450, outbound: 200 },
    { time: '10:05', inbound: 520, outbound: 230 },
    { time: '10:10', inbound: 480, outbound: 210 },
    { time: '10:15', inbound: 610, outbound: 280 },
    { time: '10:20', inbound: 590, outbound: 260 },
    { time: '10:25', inbound: 650, outbound: 300 },
    { time: '10:30', inbound: 720, outbound: 340 },
  ];

  const option: EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: '#18181b',
      borderColor: '#27272a',
      textStyle: { color: '#f4f4f5' },
    },
    legend: {
      data: ['Inbound', 'Outbound'],
      textStyle: { color: '#a1a1aa' },
      bottom: 0,
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '15%',
      top: '10%',
      containLabel: true,
    },
    xAxis: {
      type: 'category' as const,
      boundaryGap: false,
      data: chartData.map(d => d.time),
      axisLine: { lineStyle: { color: '#3f3f46' } },
      axisLabel: { color: '#a1a1aa' },
    },
    yAxis: {
      type: 'value' as const,
      axisLabel: { color: '#a1a1aa', formatter: '{value} Mbps' },
      splitLine: { lineStyle: { color: '#27272a' } },
    },
    series: [
      {
        name: 'Inbound',
        type: 'line',
        smooth: true,
        data: chartData.map(d => d.inbound),
        symbol: 'none',
        lineStyle: { color: '#3b82f6', width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(59, 130, 246, 0.2)' },
              { offset: 1, color: 'rgba(59, 130, 246, 0)' },
            ],
          },
        },
      },
      {
        name: 'Outbound',
        type: 'line',
        smooth: true,
        data: chartData.map(d => d.outbound),
        symbol: 'none',
        lineStyle: { color: '#8b5cf6', width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(139, 92, 246, 0.2)' },
              { offset: 1, color: 'rgba(139, 92, 246, 0)' },
            ],
          },
        },
      },
    ],
  };

  return <BaseChart option={option} />;
};

export default ThroughputChart;
