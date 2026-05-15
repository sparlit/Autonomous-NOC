'use client'

import React from 'react';
import BaseChart from './BaseChart';
import { EChartsOption } from 'echarts';

interface NetworkLatencyChartProps {
  data?: { time: string; value: number }[];
}

const NetworkLatencyChart: React.FC<NetworkLatencyChartProps> = ({ data }) => {
  // Fallback mock data if none provided
  const chartData = data || [
    { time: '10:00', value: 12 },
    { time: '10:05', value: 15 },
    { time: '10:10', value: 11 },
    { time: '10:15', value: 18 },
    { time: '10:20', value: 14 },
    { time: '10:25', value: 13 },
    { time: '10:30', value: 16 },
  ];

  const option: EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: '#18181b',
      borderColor: '#27272a',
      textStyle: { color: '#f4f4f5' },
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
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
      axisLine: { show: false },
      axisLabel: { color: '#a1a1aa', formatter: '{value} ms' },
      splitLine: { lineStyle: { color: '#27272a' } },
    },
    series: [
      {
        name: 'Latency',
        type: 'line',
        smooth: true,
        data: chartData.map(d => d.value),
        symbol: 'none',
        lineStyle: {
          color: '#10b981',
          width: 3,
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(16, 185, 129, 0.3)' },
              { offset: 1, color: 'rgba(16, 185, 129, 0)' },
            ],
          },
        },
      },
    ],
  };

  return <BaseChart option={option} />;
};

export default NetworkLatencyChart;
