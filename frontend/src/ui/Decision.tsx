import { CircleAlert, Radar, Scale } from 'lucide-react';
import { useFetch } from '../api/dash';
import type { DecisionMap, DecisionOption, DecisionReport, Localization, OptionId } from '../api/lab';
import { Card, Meter, Skeleton } from '.';
import { compact, fmt } from '../lib/format';

const OPT: Record<OptionId, { color: string; short: string }> = {
  fix_now: { color: 'var(--accent)', short: 'Fix now' }, isolate_fix: { color: 'var(--cool)', short: 'Isolate, fix later' },
  wait_6h: { color: 'var(--warn)', short: 'Wait 6 h' }, wait_24h: { color: 'var(--serious)', short: 'Wait 24 h' }, wait_72h: { color: 'var(--crit)', short: 'Wait 72 h' },
};

/** Where is the leak: the three likeliest pipes from the signature match. */
export function WhereCard({ data, onPick }: { data: Localization | null; onPick?: (assetId: string) => void }) {
  if (!data) return null;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><Radar size={16} />Where the leak is</span>} sub={`Matched against stored fingerprints of every pipe, ${data.sensorsUsed} of ${data.sensorsTotal} sensors used. Highlighted on the map.`}>
      <div className="stack" style={{ gap: 10 }}>
        {data.candidates.map((c) => (
          <div key={c.assetId} onClick={() => onPick?.(c.assetId)} style={onPick ? { cursor: 'pointer' } : undefined} title={onPick ? 'Show this pipe on the map' : undefined}>
            <div className="row spread" style={{ marginBottom: 3 }}><span style={{ fontWeight: c.rank === 1 ? 650 : 400 }}>{c.rank}. {c.name}</span><span className="num"><b>{fmt(c.score * 100)}%</b> <span className="muted">match{c.rank === 1 ? ` · about ${fmt(c.leakM3H)} m3/h` : ''}</span></span></div>
            <Meter value={c.score} color={c.rank === 1 ? 'var(--crit)' : 'var(--text-3)'} label={`${c.name} match`} />
          </div>
        ))}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>{data.ambiguous ? 'The top two are too close to call: inspect both.' : 'The leader is clearly ahead of the others.'} The estimated size comes from the same fit and is a calculated figure.</div>
    </Card>
  );
}

function CostBar({ o, max, best }: { o: DecisionOption; max: number; best: boolean }) {
  const pos = (v: number) => `${(v / max) * 100}%`;
  return (
    <div role="img" aria-label={`${o.name}: expected ${fmt(o.expectedCost)} EGP, between ${fmt(o.p10)} and ${fmt(o.p90)}`} style={{ position: 'relative', height: 16, background: 'var(--surface-3)', borderRadius: 4 }}>
      <div style={{ position: 'absolute', insetInlineStart: 0, width: pos(o.expectedCost), top: 3, bottom: 3, borderRadius: 3, background: OPT[o.id].color, opacity: best ? 1 : 0.75 }} />
      <div style={{ position: 'absolute', insetInlineStart: pos(o.p10), width: `calc(${pos(o.p90)} - ${pos(o.p10)})`, top: 7, height: 2, background: 'var(--text)', opacity: 0.55 }} />
      <div style={{ position: 'absolute', insetInlineStart: pos(o.p90), top: 3, bottom: 3, width: 2, background: 'var(--text)', opacity: 0.55 }} />
    </div>
  );
}

const num = (v: number) => fmt(v, Math.abs(v) < 100 ? 2 : 0);

function Map({ m, options }: { m: DecisionMap; options: OptionId[] }) {
  const flat = m.cells.flat();
  const uniform = flat.every((c) => c === flat[0]);
  const cur = (i: number, j: number) => Math.abs(m.xValues[i] - m.currentX) < 1e-9 && Math.abs(m.yValues[j] - m.currentY) < 1e-9;
  const n = m.xValues.length;
  return (
    <div>
      <div className="card-title" style={{ marginBottom: 4 }}>Does the answer change if two assumptions are wrong?</div>
      <div className="sub" style={{ fontSize: 12, marginBottom: 8 }}>The two assumptions that move this decision most, each over its plausible range. Every cell shows the cheapest option there.</div>
      <div style={{ display: 'grid', gridTemplateColumns: '18px 1fr', gap: 6 }}>
        <div className="muted" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', fontSize: 11.5, textAlign: 'center' }}>{m.yName} ({m.yUnit}): {num(m.yValues[0])} to {num(m.yValues[m.yValues.length - 1])}</div>
        <div>
          <div role="img" aria-label="Decision map" style={{ display: 'grid', gridTemplateColumns: `repeat(${n}, 26px)`, gridAutoRows: '26px', gap: 2, width: 'fit-content', margin: '0 auto' }}>
            {[...m.cells].reverse().map((row, ry) => row.map((id, i) => {
              const j = m.cells.length - 1 - ry;
              return <div key={`${ry}-${i}`} title={`${id ? OPT[id].short : 'tie'} · ${m.xName} ${num(m.xValues[i])}, ${m.yName} ${num(m.yValues[j])}`}
                style={{ borderRadius: 3, background: id ? OPT[id].color : 'var(--surface-3)', opacity: 0.85, outline: cur(i, j) ? '2px solid var(--text)' : undefined, outlineOffset: cur(i, j) ? 1 : undefined }} />;
            }))}
          </div>
          <div className="muted" style={{ fontSize: 11.5, textAlign: 'center', marginTop: 4, maxWidth: 300, marginInline: 'auto' }}>{m.xName} ({m.xUnit}): {num(m.xValues[0])} to {num(m.xValues[n - 1])}</div>
        </div>
      </div>
      <div className="legend" style={{ marginTop: 8 }}>{options.map((id) => <span key={id}><i className="swatch box" style={{ background: OPT[id].color }} />{OPT[id].short}</span>)}<span><i className="swatch box" style={{ outline: '2px solid var(--text)', background: 'transparent' }} />today&rsquo;s values</span></div>
      {uniform && <div className="chip ok" style={{ marginTop: 8, height: 'auto', padding: '4px 8px', whiteSpace: 'normal' }}>Same answer everywhere in these ranges: the decision does not depend on these two assumptions.</div>}
    </div>
  );
}

/** Fix now, isolate, or wait: expected cost, worst case, regret and where the answer would flip. */
export function DecisionStudio({ incidentId }: { incidentId: string }) {
  const { data, error } = useFetch<DecisionReport>(`/incidents/${incidentId}/decision`, 20000);
  if (error && !data) return null;
  if (!data) return <Card title={<span className="row" style={{ gap: 8 }}><Scale size={16} />Compare the options</span>}><Skeleton h={180} /></Card>;
  const max = Math.max(...data.options.map((o) => o.p90)) * 1.05;
  const rec = data.recommendedOptionId;
  const be = data.breakEven.filter((b) => b.optionId === 'wait_6h' || b.optionId === 'wait_24h');
  const fix = data.options.find((o) => o.id === 'fix_now');
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><Scale size={16} />Compare the options</span>}
      sub={`Expected cost over ${fmt(data.runs)} price scenarios. The thin line runs from a good case (P10) to a bad one (P90).`}
      right={rec ? <span className="chip ok">Cheapest: {OPT[rec].short}</span> : <span className="chip">No clear winner</span>}>
      {data.review && <div className="row" style={{ gap: 8, background: 'var(--warn-soft)', color: 'var(--warn-text)', borderRadius: 9, padding: '8px 10px', fontSize: 13, marginBottom: 12 }}><CircleAlert size={16} style={{ flexShrink: 0, marginTop: 2 }} /><span><b>Needs review.</b> {data.reviewReason}</span></div>}
      <div className="stack" style={{ gap: 12 }}>
        {data.options.map((o) => (
          <div key={o.id}>
            <div className="row spread" style={{ marginBottom: 3, gap: 12 }}>
              <span className="row" style={{ gap: 8, fontWeight: o.id === rec ? 650 : 500 }}><i className="swatch" style={{ background: OPT[o.id].color }} />{o.name}</span>
              <span className="num"><b>{fmt(o.expectedCost)}</b> <span className="muted">EGP</span></span>
            </div>
            <CostBar o={o} max={max} best={o.id === rec} />
            <div className="row wrap muted num" style={{ gap: 14, fontSize: 12, marginTop: 3 }}>
              <span>Bad case {compact(o.p90)}</span>
              <span>{o.id === 'fix_now' ? 'Reference' : `${o.netBenefitVsFixNow >= 0 ? '+' : '−'}${compact(Math.abs(o.netBenefitVsFixNow))} against fixing now`}</span>
              <span title="Average extra cost against the best option in each price scenario">Regret {compact(o.expectedRegret)}</span>
              <span>{o.winShareText}</span>
            </div>
          </div>
        ))}
      </div>
      {be.length > 0 && (
        <div className="stack" style={{ gap: 4, marginTop: 14 }}>
          {be.map((b) => (
            <div key={b.optionId} className="sub" style={{ fontSize: 13 }}>
              <b style={{ color: 'var(--text)' }}>{OPT[b.optionId].short}:</b> {b.value == null || b.value <= 0 ? `waiting costs more than fixing at any ${b.parameter === 'leak_m3h' ? 'leak size' : 'loss rate'}${fix ? '' : ''}.` : `waiting only pays if the leak is below ${fmt(b.value)} ${b.unit}.`}
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 18 }}><Map m={data.decisionMap} options={[...new Set(data.decisionMap.cells.flat().filter((x): x is OptionId => !!x))]} /></div>
      <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>Costs are calculated from the cost model&rsquo;s named formulas and assumptions; waiting longer raises the repair cost linearly, a planning choice. Regret is how much more an option costs than the best one in the same price scenario, on average.</div>
    </Card>
  );
}
