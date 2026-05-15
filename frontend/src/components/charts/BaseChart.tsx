'use client'

import React from 'react';
import ReactECharts from 'echarts-for-react';
import { EChartsOption } from 'echarts';

interface BaseChartProps {
  option: EChartsOption;
  style?: React.CSSProperties;
  className?: string;
  onEvents?: Record<string, (params: unknown) => void>;
}

const BaseChart: React.FC<BaseChartProps> = ({ option, style, className, onEvents }) => {
  const defaultStyle = {
    height: '300px',
    width: '100%',
    ...style,
  };

  return (
    <ReactECharts
      option={option}
      style={defaultStyle}
      className={className}
      theme="dark"
      onEvents={onEvents}
      notMerge={true}
      lazyUpdate={true}
    />
  );
};

export default BaseChart;
