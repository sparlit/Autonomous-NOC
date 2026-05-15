'use client'

import React from 'react';
import BaseChart from './BaseChart';
import { EChartsOption } from 'echarts';

interface DeviceStatusChartProps {
  data?: { name: string; value: number }[];
}

const DeviceStatusChart: React.FC<DeviceStatusChartProps> = ({ data }) => {
  const chartData = data || [
    { name: 'Operational', value: 142 },
    { name: 'Warning', value: 12 },
    { name: 'Critical', value: 3 },
    { name: 'Maintenance', value: 5 },
  ];

  const option: EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item' as const,
      backgroundColor: '#18181b',
      borderColor: '#27272a',
      textStyle: { color: '#f4f4f5' },
    },
    legend: {
      orient: 'vertical',
      left: 'left',
      textStyle: { color: '#a1a1aa' },
    },
    series: [
      {
        name: 'Device Status',
        type: 'pie',
        radius: ['50%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 10,
          borderColor: '#18181b',
          borderWidth: 2,
        },
        label: {
          show: false,
          position: 'center',
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 20,
            fontWeight: 'bold',
            color: '#f4f4f5',
          },
        },
        labelLine: {
          show: false,
        },
        data: chartData.map(d => ({
          ...d,
          itemStyle: {
            color: d.name === 'Operational' ? '#10b981' : 
                   d.name === 'Warning' ? '#f59e0b' :
                   d.name === 'Critical' ? '#ef4444' : '#6366f1'
          }
        })),
      },
    ],
  };

  return <BaseChart option={option} style={{ height: '250px' }} />;
};

export default DeviceStatusChart;
