import { useState } from 'react';
import { colPath, linear, niceDomain, niceTicks, useWidth } from './scale';
import { fmt } from '../lib/format';

export interface Col { key: string; label: string; value: number; color: string; note?: string }
interface Props { data: Col[]; height?: number; unit?: string; digits?: number; target?: { v: number; label: string }; yDomain?: [number, number]; label: string; tick?: (c: Col, i: number) => string | null; axisDigits?: number }

const M = { l: 44, r: 12, t: 12, b: 26 };

/** Columns: ≤24px thick, 4px rounded data-end, square baseline, 2px surface gap, hover tooltip. */
export function Columns({ data, height = 200, unit = '', digits = 0, target, yDomain, label, tick, axisDigits }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const W = Math.max(width, 240), iw = W - M.l - M.r, ih = height - M.t - M.b;
  const vals = data.map((d) => d.value).concat(target ? [target.v] : []);
  const [lo, hi] = yDomain ?? niceDomain(Math.min(0, ...vals), Math.max(...vals), 0.08);
  const sy = linear(lo, hi, M.t + ih, M.t);
  const slot = iw / Math.max(data.length, 1), bw = Math.min(24, Math.max(3, slot - 2));
  const ticks = niceTicks(lo, hi, 4);
  const tipLeft = hover !== null ? Math.min(Math.max(M.l + slot * hover + slot / 2 + 10, 4), W - 180) : 0;
  return (
    <div className="chart" ref={ref}>
      {width === 0 && <div style={{ height }} />}
      {width > 0 && <svg width={W} height={height} role="img" aria-label={label} onPointerLeave={() => setHover(null)}>
        {ticks.map((v) => <g key={v}><line x1={M.l} x2={M.l + iw} y1={sy(v)} y2={sy(v)} stroke="var(--grid)" /><text x={M.l - 8} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="var(--text-3)">{fmt(v, axisDigits ?? (v % 1 ? 2 : 0))}</text></g>)}
        <line x1={M.l} x2={M.l + iw} y1={sy(Math.max(lo, 0))} y2={sy(Math.max(lo, 0))} stroke="var(--axis)" />
        {data.map((d, i) => {
          const x = M.l + slot * i + (slot - bw) / 2;
          return <g key={d.key} onPointerEnter={() => setHover(i)}>
            <rect x={M.l + slot * i} y={M.t} width={slot} height={ih} fill="transparent" />
            <path d={colPath(x, sy(d.value), bw, sy(Math.max(lo, 0)))} fill={d.color} opacity={hover === null || hover === i ? 1 : .55} />
            {tick?.(d, i) && <text x={x + bw / 2} y={height - 8} textAnchor="middle" fontSize="11" fill="var(--text-3)">{tick(d, i)}</text>}
          </g>;
        })}
        {target && <g><line x1={M.l} x2={M.l + iw} y1={sy(target.v)} y2={sy(target.v)} stroke="var(--text)" strokeOpacity=".7" strokeDasharray="5 4" /><text x={M.l + iw} y={sy(target.v) - 5} textAnchor="end" fontSize="11" fill="var(--text-2)">{target.label}</text></g>}
      </svg>}
      {hover !== null && data[hover] && <div className="tooltip" style={{ left: tipLeft, top: 4 }}>
        <div className="muted">{data[hover].label}</div>
        <b className="num">{fmt(data[hover].value, digits)} {unit}</b>
        {data[hover].note && <div className="muted">{data[hover].note}</div>}
      </div>}
    </div>
  );
}
