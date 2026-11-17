import { useState } from 'react';
import { niceTicks, useWidth } from './scale';
import { fmt } from '../lib/format';

interface Row { name: string; label: string; mae: number; isLive: boolean }

/** Horizontal test MAE bars: direct labels, production emphasis and a shared zero. */
export function ModelBars({ rows, color, unit }: { rows: Row[]; color: string; unit: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const maximum = Math.max(...rows.map((row) => row.mae), .001) * 1.05;
  const W = Math.max(width, 1), H = rows.length * 60 + 24;
  return <div ref={ref} className="chart">
    <svg width="100%" height={H} role="img" aria-label={`Test MAE by model in ${unit}; lower is better`} onPointerLeave={() => setHover(null)}>
      {niceTicks(0, maximum, 4).map((tick) => <g key={tick}>
        <line x1={tick / maximum * W} x2={tick / maximum * W} y1={0} y2={H - 20} stroke="var(--grid)" />
        <text x={tick / maximum * W} y={H - 3} fill="var(--text-3)" fontSize={11}>{fmt(tick, tick % 1 ? 1 : 0)}</text>
      </g>)}
      {rows.map((row, i) => {
        const end = row.mae / maximum * W, y = i * 60 + 25, radius = Math.min(4, end / 2);
        return <g key={row.name} onPointerEnter={() => setHover(row.name)}>
          <title>{row.label}{row.isLive ? ' (production)' : ''}: {fmt(row.mae, 3)} {unit} MAE</title>
          <rect width={W} height={58} y={i * 60} fill="transparent" />
          <text x={0} y={i * 60 + 15} fontSize={12} fontWeight={row.isLive ? 650 : 400} fill="var(--text)">{row.label}{row.isLive ? ' · production' : ''}</text>
          <text x={W} y={i * 60 + 15} textAnchor="end" fontSize={12} fill="var(--text)">{fmt(row.mae, 2)}</text>
          <path d={`M0,${y}H${end - radius}Q${end},${y} ${end},${y + radius}V${y + 20 - radius}Q${end},${y + 20} ${end - radius},${y + 20}H0Z`}
            fill={row.isLive ? color : 'var(--text-3)'} opacity={hover === null || hover === row.name ? 1 : .55} />
        </g>;
      })}
    </svg>
  </div>;
}
