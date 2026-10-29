import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Droplets, Snowflake, TriangleAlert, CircleCheck, CircleX, Zap, X, TrendingDown, TrendingUp, Minus, type LucideIcon } from 'lucide-react';
import type { AssetStatus, Network } from '../api/types';
import { Sparkline } from '../charts/Sparkline';

export const NET: Record<Network, { icon: LucideIcon; color: string; soft: string; label: string }> = {
  water: { icon: Droplets, color: 'var(--water)', soft: 'var(--water-soft)', label: 'Water' },
  electricity: { icon: Zap, color: 'var(--elec)', soft: 'var(--elec-soft)', label: 'Electricity' },
  cooling: { icon: Snowflake, color: 'var(--cool)', soft: 'var(--cool-soft)', label: 'Cooling' },
};
export const NetIcon = ({ n, size = 16 }: { n: Network; size?: number }) => { const I = NET[n].icon; return <I size={size} color={NET[n].color} strokeWidth={2.2} aria-hidden />; };

/** Status is never colour alone: icon + label + colour. */
export const STATUS: Record<AssetStatus | 'open' | 'resolved', { label: string; cls: string; icon: LucideIcon }> = {
  normal: { label: 'Normal', cls: 'ok', icon: CircleCheck },
  warning: { label: 'Warning', cls: 'warn', icon: TriangleAlert },
  fault: { label: 'Fault', cls: 'crit', icon: CircleX },
  offline: { label: 'Offline', cls: '', icon: CircleX },
  open: { label: 'Open', cls: 'crit', icon: TriangleAlert },
  resolved: { label: 'Resolved', cls: 'ok', icon: CircleCheck },
};
export const StatusChip = ({ s, label }: { s: keyof typeof STATUS; label?: string }) => { const x = STATUS[s]; const I = x.icon; return <span className={`chip ${x.cls}`}><I size={13} aria-hidden />{label ?? x.label}</span>; };
export const SeverityChip = ({ s }: { s: 'info' | 'warning' | 'critical' }) =>
  <span className={`chip ${s === 'critical' ? 'crit' : s === 'warning' ? 'warn' : ''}`}>{s === 'critical' ? <CircleX size={13} aria-hidden /> : <TriangleAlert size={13} aria-hidden />}{s === 'critical' ? 'Critical' : s === 'warning' ? 'Warning' : 'Info'}</span>;

export function Card({ title, sub, right, children, pad = true, className = '', style }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; pad?: boolean; className?: string; style?: React.CSSProperties }) {
  return (
    <section className={`card ${className}`} style={style}>
      {(title || right) && <header className="card-head"><div><div className="card-title">{title}</div>{sub && <div className="card-sub">{sub}</div>}</div>{right}</header>}
      <div className={pad ? 'card-body' : ''}>{children}</div>
    </section>
  );
}

export const Skeleton = ({ h = 16, w = '100%', style }: { h?: number; w?: number | string; style?: React.CSSProperties }) => <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden />;
export const Empty = ({ title, children }: { title: string; children?: ReactNode }) => <div className="empty"><b>{title}</b>{children}</div>;

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return <div className="seg" role="group" aria-label={label}>{options.map((o) => <button key={o.v} type="button" aria-pressed={value === o.v} onClick={() => onChange(o.v)}>{o.label}</button>)}</div>;
}

export function Drawer({ title, onClose, children, foot, width }: { title: ReactNode; onClose: () => void; children: ReactNode; foot?: ReactNode; width?: number }) {
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  return createPortal(<>
    <div className="scrim" onClick={onClose} />
    <aside className="drawer" role="dialog" aria-modal="true" style={width ? { width: `min(${width}px, 100vw)` } : undefined}>
      <header className="drawer-head"><h2>{title}</h2><button className="btn ghost icon sm" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
      <div className="drawer-body">{children}</div>
      {foot && <footer className="drawer-foot">{foot}</footer>}
    </aside>
  </>, document.body);
}

export interface KpiProps {
  label: string; value: string; unit?: string; color: string; spark?: number[]; target?: number; foot?: string;
  delta?: { text: string; good: boolean | null; dir: 'up' | 'down' | 'flat' }; onClick?: () => void; loading?: boolean; icon?: ReactNode; viz?: ReactNode;
}
export function Kpi({ label, value, unit, color, spark, target, foot, delta, onClick, loading, icon, viz }: KpiProps) {
  const body = <>
    <div className="kpi-label"><span className="row" style={{ gap: 6 }}>{icon}{label}</span>
      {delta && <span className={`delta ${delta.good === null ? 'flat' : delta.good ? 'good' : 'bad'}`}>{delta.dir === 'up' ? <TrendingUp size={13} aria-hidden /> : delta.dir === 'down' ? <TrendingDown size={13} aria-hidden /> : <Minus size={13} aria-hidden />}{delta.text}</span>}
    </div>
    {loading ? <Skeleton h={30} w={110} style={{ marginTop: 6 }} /> : <div className="kpi-value"><b>{value}</b>{unit && <span className="kpi-unit">{unit}</span>}</div>}
    <div style={{ margin: '6px 0 2px' }}>{loading ? <Skeleton h={34} /> : viz ? viz : spark ? <Sparkline values={spark} color={color} target={target} label={`${label} trend`} /> : <div style={{ height: 36 }} />}</div>
    <div className="kpi-foot">{foot ?? ' '}</div>
  </>;
  return onClick
    ? <button type="button" className="card kpi" onClick={onClick} style={{ cursor: 'pointer', font: 'inherit', color: 'inherit' }}>{body}</button>
    : <div className="card kpi">{body}</div>;
}

export const Meter = ({ value, color = 'var(--accent)', label }: { value: number; color?: string; label: string }) => (
  <div role="meter" aria-label={label} aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100} style={{ height: 8, borderRadius: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
    <div style={{ height: '100%', width: `${Math.max(0, Math.min(1, value)) * 100}%`, background: color, borderRadius: 4, transition: 'width var(--t-med) var(--ease)' }} />
  </div>
);
