import { useState } from 'react';
import { linear, niceTicks, useWidth } from './scale';
import { fmt } from '../lib/format';

export interface CurveSeries { id: string; label: string; color: string; dash?: boolean; values: { delay: number | null; rate: number }[] }
interface Props { xs: number[]; series: CurveSeries[]; height?: number; label: string; xUnit: string; cap: number }

const M = { l: 48, r: 16, t: 14, b: 40 };

/** Detection delay against leak size, one line per detector. A size where a detector missed some runs is drawn hollow; a size it missed entirely sits on the top edge as a cross. */
export function LeakCurve({ xs, series, height = 260, label, xUnit, cap }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const W = Math.max(width, 280), iw = W - M.l - M.r, ih = height - M.t - M.b;
  const maxY = Math.max(10, ...series.flatMap((s) => s.values.map((v) => v.delay ?? 0)));
  const ticks = niceTicks(0, maxY * 1.05, 4);
  const top = ticks[ticks.length - 1];
  const sy = linear(0, top, M.t + ih, M.t);
  const sx = (i: number) => M.l + (xs.length === 1 ? iw / 2 : (i / (xs.length - 1)) * iw);
  const tipLeft = hover !== null ? Math.min(Math.max(sx(hover) + 12, 4), W - 200) : 0;
  return (
    <div className="chart" ref={ref}>
      {width === 0 && <div style={{ height }} />}
      {width > 0 && (
        <svg width={W} height={height} role="img" aria-label={label} onPointerLeave={() => setHover(null)}
          onPointerMove={(e) => { const x = e.clientX - e.currentTarget.getBoundingClientRect().left; setHover(Math.max(0, Math.min(xs.length - 1, Math.round(((x - M.l) / iw) * (xs.length - 1))))); }}>
          {ticks.map((v) => <g key={v}><line x1={M.l} x2={M.l + iw} y1={sy(v)} y2={sy(v)} stroke="var(--grid)" /><text x={M.l - 8} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="var(--text-3)">{fmt(v)}</text></g>)}
          <text x={12} y={M.t + ih / 2} fontSize="11" fill="var(--text-3)" transform={`rotate(-90 12 ${M.t + ih / 2})`} textAnchor="middle">minutes to detect</text>
          {xs.map((x, i) => <text key={x} x={sx(i)} y={height - 22} textAnchor="middle" fontSize="11" fill="var(--text-3)">{fmt(x)}</text>)}
          <text x={M.l + iw / 2} y={height - 5} textAnchor="middle" fontSize="11" fill="var(--text-3)">leak size, {xUnit}</text>
          <line x1={M.l} x2={M.l + iw} y1={M.t + ih} y2={M.t + ih} stroke="var(--axis)" />
          {hover !== null && <line x1={sx(hover)} x2={sx(hover)} y1={M.t} y2={M.t + ih} stroke="var(--border-strong)" />}
          {series.map((s) => {
            const pts = s.values.map((v, i) => (v.delay === null ? null : [sx(i), sy(v.delay)] as [number, number]));
            const segs: [number, number][][] = []; let cur: [number, number][] = [];
            pts.forEach((p) => { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }); if (cur.length) segs.push(cur);
            return (
              <g key={s.id}>
                {segs.map((seg, k) => <path key={k} d={seg.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join('')} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash ? '5 4' : undefined} strokeLinejoin="round" />)}
                {s.values.map((v, i) => v.delay === null
                  ? <text key={i} x={sx(i)} y={M.t + 4} textAnchor="middle" fontSize="12" fill={s.color} fontWeight="700">×</text>
                  : <circle key={i} cx={sx(i)} cy={sy(v.delay)} r={4} fill={v.rate >= 0.999 ? s.color : 'var(--surface)'} stroke={s.color} strokeWidth={2} />)}
              </g>
            );
          })}
        </svg>
      )}
      {hover !== null && (
        <div className="tooltip" style={{ left: tipLeft, top: 4 }}>
          <div className="muted">Leak of {fmt(xs[hover])} {xUnit}</div>
          {series.map((s) => { const v = s.values[hover]; return (
            <div key={s.id} className="row spread" style={{ gap: 14 }}><span className="row" style={{ gap: 6 }}><i className="swatch" style={{ background: s.color }} />{s.label}</span>
              <b className="num">{v.delay === null ? `missed (cap ${cap} min)` : `${fmt(v.delay)} min`}{v.delay !== null && v.rate < 0.999 ? ` · found ${fmt(v.rate * 100)}%` : ''}</b></div>); })}
        </div>
      )}
    </div>
  );
}
