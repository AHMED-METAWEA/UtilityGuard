import type { ReactNode } from 'react';
import { SlidersHorizontal } from 'lucide-react';
import type { Calculation, Cost, EvidenceType, Explanation } from '../api/types';
import { substitutedFormula } from '../lib/present';
import { compact, fmt } from '../lib/format';
import { useUi } from '../store/ui';

const TAG: Record<EvidenceType, { cls: string; label: string }> = {
  measured: { cls: 'accent', label: 'measured' }, forecast: { cls: 'ok', label: 'forecast' }, rule: { cls: '', label: 'rule' }, assumed: { cls: 'warn', label: 'assumed' },
};
export const EvidenceChip = ({ t }: { t: EvidenceType }) => <span className={`chip ${TAG[t].cls}`}>{TAG[t].label}</span>;

/** P10 to P90 of the saving with the P50 marked. Scale and labels come straight from the backend range. */
export function RangeBar({ p10, p50, p90, runs, unit = 'EGP' }: { p10: number; p50: number; p90: number; runs: number; unit?: string }) {
  const lo = Math.min(p10 * 0.85, p10 - (p90 - p10) * 0.3), hi = p90 + (p90 - p10) * 0.3;
  const pos = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div>
      <div className="row spread" style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 6 }}><span>Likely range of the saving</span><span className="muted">{fmt(runs)} simulated runs</span></div>
      <div style={{ position: 'relative', height: 26, background: 'var(--surface-2)', borderRadius: 6, border: '1px solid var(--border)' }}>
        <div style={{ position: 'absolute', insetInlineStart: pos(p10), width: `calc(${pos(p90)} - ${pos(p10)})`, top: 8, height: 8, borderRadius: 4, background: 'color-mix(in srgb, var(--ok) 55%, transparent)' }} />
        <div style={{ position: 'absolute', insetInlineStart: pos(p50), top: 4, width: 3, height: 16, borderRadius: 2, background: 'var(--ok-text)', marginInlineStart: -1 }} />
      </div>
      <div className="row spread num" style={{ fontSize: 12, marginTop: 5 }}>
        <span><span className="muted">P10 </span>{fmt(p10)}</span><span><b>P50 {fmt(p50)}</b></span><span><span className="muted">P90 </span>{fmt(p90)}</span>
        <span className="muted" style={{ display: 'none' }}>{unit}</span>
      </div>
    </div>
  );
}

export function Reasons({ reasons }: { reasons: Explanation['reasons'] }) {
  return (
    <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 10 }}>
      {reasons.map((r, i) => (
        <li key={i} style={{ display: 'grid', gridTemplateColumns: '22px 1fr auto', gap: 8, alignItems: 'start', lineHeight: 1.45 }}>
          <span className="num muted">{i + 1}</span><span>{r.text}</span><EvidenceChip t={r.evidenceType} />
        </li>
      ))}
    </ol>
  );
}

const unitOf = (c: Calculation) => (c.unit === 'EGP/h' ? 'EGP/h' : c.unit);

/** The calculation record as a reader sees it: every formula with its real values, then the assumptions. */
export function CalcView({ cost, explanation, heading }: { cost: Cost; explanation: Explanation; heading?: ReactNode }) {
  const set = useUi((s) => s.set);
  return (
    <div className="stack" style={{ gap: 16 }}>
      {heading}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8 }}>
        {[['Act now', cost.actNow], [`Wait ${fmt(cost.delayHours)} h`, cost.costOfDelay], ['Saving', cost.expectedSaving]].map(([l, v], i) => (
          <div key={l as string} style={{ background: i === 2 ? 'var(--ok-soft)' : 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '8px 10px' }}>
            <div className="sub" style={{ fontSize: 12 }}>{l}</div><div className="num" style={{ fontSize: 18, fontWeight: 650 }}>{fmt(v as number)} <span className="muted" style={{ fontSize: 11 }}>EGP</span></div>
          </div>
        ))}
      </div>
      {cost.savingRange && <RangeBar {...cost.savingRange} />}
      <div>
        <div className="card-title" style={{ marginBottom: 8 }}>How each number was reached</div>
        <div className="stack" style={{ gap: 8 }}>
          {explanation.calculations.map((c, i) => (
            <div key={i} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 9, padding: '8px 10px' }}>
              <div className="row spread" style={{ gap: 12 }}><span style={{ fontWeight: 500 }}>{c.name}</span><b className="num" style={{ whiteSpace: 'nowrap' }}>{fmt(c.result)} {unitOf(c)}</b></div>
              <div className="num muted" style={{ fontSize: 12, marginTop: 2, overflowWrap: 'anywhere' }}>{c.formula}</div>
              <div className="num" style={{ fontSize: 12, color: 'var(--text-2)', overflowWrap: 'anywhere' }}>{substitutedFormula(c)}</div>
            </div>
          ))}
        </div>
      </div>
      {explanation.sensitivity.length > 0 && (
        <div>
          <div className="card-title" style={{ marginBottom: 6 }}>If an assumption changes by 20%</div>
          <div className="stack" style={{ gap: 4 }}>
            {[...new Set(explanation.sensitivity.map((s) => s.input))].map((k) => {
              const rows = explanation.sensitivity.filter((s) => s.input === k).sort((a, b) => a.changePercent - b.changePercent);
              const name = explanation.assumptions.find((a) => a.key === k)?.name ?? k;
              return <div key={k} className="row spread sub" style={{ fontSize: 13 }}><span>{name}</span><span className="num">{rows.map((r) => `${r.changePercent > 0 ? '+' : ''}${r.changePercent}% → ${compact(r.newSaving)}`).join('   ')}</span></div>;
            })}
          </div>
        </div>
      )}
      <div>
        <div className="row spread" style={{ marginBottom: 8 }}><div className="card-title">Assumptions used</div>
          <button className="btn sm" onClick={() => set({ assumptionsOpen: true })}><SlidersHorizontal size={14} />Edit assumptions</button></div>
        <table className="table"><tbody>
          {explanation.assumptions.map((a) => (
            <tr key={a.key}><td>{a.name}{a.userChanged && <span className="chip warn" style={{ marginInlineStart: 8 }}>changed</span>}</td><td className="r num">{fmt(a.value, a.value % 1 ? 2 : 0)} <span className="muted">{a.unit}</span></td></tr>
          ))}
        </tbody></table>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>Confidence {fmt(explanation.confidence * 100)}%. Every figure is computed by the cost model; the AI only explains it.</div>
    </div>
  );
}
