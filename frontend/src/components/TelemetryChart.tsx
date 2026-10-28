import ReactECharts from 'echarts-for-react';
import { useTranslation } from 'react-i18next';
import type { Reading, Sensor } from '../api/types';
import { colors } from '../design/tokens';
import { NumberValue } from '../design/primitives';

export default function TelemetryChart({ sensor, readings }: { sensor: Sensor; readings: Reading[] }) {
  const { t } = useTranslation();
  const latest = readings.at(-1);
  return <article className="signal-card"><div className="signal-label"><span style={{ color: colors[sensor.network] }}>{t(`networks.${sensor.network}`)}</span><span>{sensor.id}</span></div>{latest && <NumberValue value={latest.value} unit={sensor.unit} digits={2} />}
    <ReactECharts style={{ height: 76 }} opts={{ renderer: 'canvas' }} option={{
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      animationDuration: 200, animationDurationUpdate: 200,
      grid: { left: 2, right: 2, top: 10, bottom: 4 },
      tooltip: { trigger: 'axis', backgroundColor: colors.panel, borderColor: colors.border, textStyle: { color: colors.ink }, valueFormatter: (value: unknown) => `${Number(value).toLocaleString()} ${sensor.unit}`, confine: true },
      xAxis: { type: 'category', show: false, data: readings.map((r) => new Date(r.timestamp).toLocaleTimeString()) },
      yAxis: { type: 'value', show: false, scale: true },
      series: [
        { name: t('charts.measured'), type: 'line', showSymbol: false, smooth: 0.25, lineStyle: { width: 2, color: colors[sensor.network] }, areaStyle: { color: colors[sensor.network], opacity: 0.07 }, data: readings.map((r) => r.quality === 'missing' ? null : r.value) },
        { name: t('charts.forecast'), type: 'line', showSymbol: false, lineStyle: { width: 1, type: 'dashed', color: colors.muted }, data: readings.map((r) => r.forecast ?? null) },
      ],
    }} />
  </article>;
}
