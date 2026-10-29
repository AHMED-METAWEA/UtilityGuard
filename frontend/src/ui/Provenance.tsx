import type { Assumption, AssumptionStatus, TornadoRow } from '../api/types';
import { compact, fmt } from '../lib/format';

const STATUS: Record<AssumptionStatus, { cls: string; label: string; hint: string }> = {
  official: { cls: 'ok', label: 'Official', hint: 'Taken from an official published figure' },
  benchmark: { cls: 'accent', label: 'Benchmark', hint: 'A published engineering or industry benchmark' },
  engineering: { cls: 'accent', label: 'Engineering', hint: 'Follows from a physical law or design rule' },
  planning: { cls: 'warn', label: 'Planning figure', hint: 'An illustrative figure to verify before operational use' },
};
export const StatusTag = ({ s }: { s: AssumptionStatus }) => <span className={`chip ${STATUS[s].cls}`} title={STATUS[s].hint}>{STATUS[s].label}</span>;

const num = (v: number) => fmt(v, Math.abs(v) < 10 && v % 1 ? 2 : Math.abs(v) < 1000 && v % 1 ? 1 : 0);

/** Where an assumption comes from: its status, why the value was chosen, the range it is varied over and when it was last reviewed. */
export function Basis({ a }: { a: Pick<Assumption, 'basis' | 'status' | 'low' | 'high' | 'unit' | 'reviewed'> }) {
  return (
    <div className="stack" style={{ gap: 4, fontSize: 12, lineHeight: 1.45 }}>
      <div className="row wrap" style={{ gap: 8 }}><StatusTag s={a.status} /><span className="muted num">Varied between {num(a.low)} and {num(a.high)} {a.unit}</span><span className="muted">Reviewed {a.reviewed}</span></div>
      <div style={{ color: 'var(--text-2)' }}>{a.basis}</div>
    </div>
  );
}

/** Which assumption moves the saving most: each bar runs from the saving at the assumption's low value to the saving at its high value. */
export function Tornado({ rows, base }: { rows: TornadoRow[]; base: number }) {
  const shown = rows.slice(0, 6);
  if (!shown.length) return null;
  const vals = shown.flatMap((r) => [r.savingAtLow, r.savingAtHigh]).concat(base);
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const pos = (v: number) => `${((v - lo) / span) * 100}%`;
  return (
    <div className="stack" style={{ gap: 10 }} role="img" aria-label="Sensitivity of the saving to each assumption">
      {shown.map((r) => {
        const a = Math.min(r.savingAtLow, r.savingAtHigh), b = Math.max(r.savingAtLow, r.savingAtHigh);
        return (
          <div key={r.input}>
            <div className="row spread" style={{ fontSize: 12.5, marginBottom: 3, gap: 12 }}>
              <span style={{ minWidth: 0 }}>{r.name}</span>
              <span className="num muted" style={{ whiteSpace: 'nowrap' }}>{num(r.low)} to {num(r.high)}</span>
            </div>
            <div style={{ position: 'relative', height: 14, background: 'var(--surface-3)', borderRadius: 4 }}>
              <div style={{ position: 'absolute', insetInlineStart: pos(a), width: `calc(${pos(b)} - ${pos(a)})`, minWidth: 3, top: 2, bottom: 2, borderRadius: 3, background: 'var(--accent)', opacity: 0.8 }} />
              <div style={{ position: 'absolute', insetInlineStart: pos(base), top: -2, bottom: -2, width: 2, background: 'var(--text)', opacity: 0.7 }} />
            </div>
            <div className="row spread num muted" style={{ fontSize: 11.5, marginTop: 2 }}><span>{compact(a)}</span><span>{compact(b)}</span></div>
          </div>
        );
      })}
      <div className="muted" style={{ fontSize: 12 }}>The dark line is the saving now ({compact(base)} EGP). Each bar shows how far it moves when that one assumption goes from its low to its high value.</div>
    </div>
  );
}
