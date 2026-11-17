import { useMemo, useState } from 'react';
import { area, band as bandPath, line, linear, niceDomain, niceTicks, timeTicks, useWidth } from './scale';
import { fmt, hhmm } from '../lib/format';

export interface Pt { t: number; v: number | null }
export interface Series { id: string; label: string; color: string; points: Pt[]; dash?: boolean; width?: number; fill?: boolean }
export interface BandSeries { label: string; color: string; points: { t: number; lo: number; hi: number }[] }
export interface Threshold { v: number; label: string; color: string }

interface Props {
  series: Series[]; band?: BandSeries; thresholds?: Threshold[]; nowT?: number;
  height?: number; unit?: string; digits?: number; yDomain?: [number, number]; xDomain?: [number, number];
  yTick?: (v: number) => string; xTick?: (t: number) => string; label: string;
}

const M = { l: 44, r: 16, t: 14, b: 24 };

/** Multi-series time chart: 2px lines, ~10% area wash, band, crosshair tooltip, direct end labels for the lead series. */
export function TimeChart({ series, band, thresholds = [], nowT, height = 220, unit = '', digits = 0, yDomain, xDomain, yTick, xTick = hhmm, label }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const W = Math.max(width, 240), H = height, iw = W - M.l - M.r, ih = H - M.t - M.b;

  const dom = useMemo(() => {
    const ts = [...series.flatMap((s) => s.points.map((p) => p.t)), ...(band?.points.map((p) => p.t) ?? [])];
    const vs = [...series.flatMap((s) => s.points.map((p) => p.v).filter((v): v is number => v !== null)),
      ...(band?.points.flatMap((p) => [p.lo, p.hi]) ?? []), ...thresholds.map((t) => t.v)];
    const x: [number, number] = xDomain ?? [Math.min(...ts), Math.max(...ts)];
    const y = yDomain ?? (vs.length ? niceDomain(Math.min(...vs), Math.max(...vs)) : [0, 1] as [number, number]);
    return { x, y };
  }, [series, band, thresholds, xDomain, yDomain]);

  const sx = linear(dom.x[0], dom.x[1], M.l, M.l + iw), sy = linear(dom.y[0], dom.y[1], M.t + ih, M.t);
  const yTicks = niceTicks(dom.y[0], dom.y[1], Math.max(3, Math.round(ih / 44)));
  const xTicks = timeTicks(dom.x[0], dom.x[1], Math.max(3, Math.floor(iw / 70)));
  const toXY = (pts: Pt[]) => pts.filter((p) => p.v !== null).map((p) => [sx(p.t), sy(p.v as number)] as [number, number]);

  const times = useMemo(() => [...new Set(series.flatMap((s) => s.points.map((p) => p.t)))].sort((a, b) => a - b), [series]);
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect(); const x = e.clientX - r.left;
    if (x < M.l || x > M.l + iw || !times.length) { setHover(null); return; }
    const t = dom.x[0] + ((x - M.l) / iw) * (dom.x[1] - dom.x[0]);
    let best = times[0]; for (const c of times) if (Math.abs(c - t) < Math.abs(best - t)) best = c;
    setHover(best);
  };
  const at = (s: Series, t: number) => s.points.find((p) => p.t === t)?.v ?? null;
  const bandAt = band?.points.find((p) => p.t === hover);
  const yFmt = yTick ?? ((v: number) => (Math.abs(v) >= 1000 ? `${fmt(v / 1000, v % 1000 ? 1 : 0)}k` : fmt(v, v % 1 ? 1 : 0)));
  const tipLeft = hover !== null ? Math.min(Math.max(sx(hover) + 12, 4), W - 190) : 0;

  return (
    <div className="chart" ref={ref}>
      {width === 0 && <div style={{ height }} />}
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {yTicks.map((v) => <g key={v}><line x1={M.l} x2={M.l + iw} y1={sy(v)} y2={sy(v)} stroke="var(--grid)" /><text x={M.l - 8} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="var(--text-3)">{yFmt(v)}</text></g>)}
          {xTicks.map((t) => <text key={t} x={sx(t)} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--text-3)">{xTick(t)}</text>)}
          <line x1={M.l} x2={M.l + iw} y1={M.t + ih} y2={M.t + ih} stroke="var(--axis)" />
          {band && <path d={bandPath(band.points.map((p) => [sx(p.t), sy(p.hi)]), band.points.map((p) => [sx(p.t), sy(p.lo)]))} fill={band.color} fillOpacity=".14" />}
          {thresholds.map((th) => <g key={th.label}><line x1={M.l} x2={M.l + iw} y1={sy(th.v)} y2={sy(th.v)} stroke={th.color} strokeDasharray="5 4" /><text x={M.l + iw} y={sy(th.v) - 5} textAnchor="end" fontSize="11" fill="var(--text-2)">{th.label}</text></g>)}
          {nowT !== undefined && nowT >= dom.x[0] && nowT <= dom.x[1] && <g><line x1={sx(nowT)} x2={sx(nowT)} y1={M.t} y2={M.t + ih} stroke="var(--border-strong)" strokeDasharray="2 3" /><text x={sx(nowT)} y={M.t - 3} textAnchor="middle" fontSize="11" fill="var(--text-2)">now</text></g>}
          {series.map((s) => {
            const xy = toXY(s.points);
            return <g key={s.id}>
              {s.fill && <path d={area(xy, M.t + ih)} fill={s.color} fillOpacity=".10" />}
              <path d={line(xy)} fill="none" stroke={s.color} strokeWidth={s.width ?? 2} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={s.dash ? '5 4' : undefined} />
            </g>;
          })}
          {series.length <= 4 && series.filter((s) => !s.dash).map((s) => {
            const last = [...s.points].reverse().find((p) => p.v !== null); if (!last) return null;
            return <circle key={s.id} cx={sx(last.t)} cy={sy(last.v as number)} r="4" fill={s.color} stroke="var(--surface)" strokeWidth="2" />;
          })}
          {hover !== null && <g>
            <line x1={sx(hover)} x2={sx(hover)} y1={M.t} y2={M.t + ih} stroke="var(--text-3)" strokeOpacity=".6" />
            {series.map((s) => { const v = at(s, hover); return v === null ? null : <circle key={s.id} cx={sx(hover)} cy={sy(v)} r="4.5" fill={s.color} stroke="var(--surface)" strokeWidth="2" />; })}
          </g>}
        </svg>
      )}
      {hover !== null && (
        <div className="tooltip" style={{ left: tipLeft, top: 8 }}>
          <div className="muted" style={{ marginBottom: 4 }}>{xTick(hover)}</div>
          {series.map((s) => { const v = at(s, hover); return v === null ? null : (
            <div key={s.id} className="row spread" style={{ gap: 14 }}><span className="row" style={{ gap: 6 }}><span className="swatch" style={{ background: s.color }} />{s.label}</span><b className="num">{fmt(v, digits)} {unit}</b></div>); })}
          {bandAt && <div className="row spread muted" style={{ gap: 14, marginTop: 2 }}><span>{band?.label}</span><span className="num">{fmt(bandAt.lo, digits)} to {fmt(bandAt.hi, digits)}</span></div>}
        </div>
      )}
      {series.length > 1 && <div className="legend" style={{ paddingInlineStart: M.l }}>
        {series.map((s) => <span key={s.id}><i className="swatch" style={{ background: s.color, ...(s.dash ? { background: `repeating-linear-gradient(90deg, ${s.color} 0 4px, transparent 4px 7px)` } : {}) }} />{s.label}</span>)}
        {band && <span><i className="swatch box" style={{ background: band.color, opacity: .35 }} />{band.label}</span>}
      </div>}
    </div>
  );
}
