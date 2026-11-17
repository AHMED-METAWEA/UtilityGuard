import { Fragment, useState } from 'react';
import { useCityDecision, useDecisionCost, type CostNetwork, type CostPart, type DecisionRow } from '../api/city';
import { BAND_LABEL, BAND_VAR, GROUPS, groupCounts, hospitalLine, pressureLine, sortDecision } from '../lib/city';
import { compact, fmt } from '../lib/format';
import { Card, Skeleton } from '.';

/** The twin's outage cost, the healthcare team's environmental stress and their hospitals, district by district and side by side. Nothing from the two sides is
 * added together: the group only says whether each side is above or below the median of the districts. */
const NETS: { id: CostNetwork; label: string }[] = [{ id: 'any', label: 'Worst of all' }, { id: 'electricity', label: 'Electricity' }, { id: 'water', label: 'Water' }, { id: 'cooling', label: 'Cooling' }];
const KIND: Record<string, string> = { substation_trip: 'A substation trips', pump_failure: 'A district pump fails', pipe_burst: 'A district main bursts', cooling_fault: 'A cooling plant faults' };
const n2 = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 2 });

/** Where one cost figure comes from: the failure behind it, every cost line with its formula and inputs, and the assumptions, as the cost engine wrote them. */
function CostHow({ row }: { row: DecisionRow }) {
  const [net, setNet] = useState<CostNetwork>('any');
  const { data, error } = useDecisionCost(row.districtKey, net);
  return (
    <div style={{ padding: '4px 16px 16px' }}>
      <div className="row wrap" style={{ gap: 6, marginBottom: 10 }} role="tablist" aria-label="Network">
        {NETS.map((n) => <button key={n.id} type="button" role="tab" aria-selected={net === n.id} className={`btn sm${net === n.id ? ' primary' : ''}`} onClick={() => setNet(n.id)}>{n.label}</button>)}
      </div>
      {error ? <div className="muted">Could not load the explanation: {error}</div> : !data ? <Skeleton h={80} /> : <>
        <p style={{ margin: '0 0 10px', fontSize: 13, lineHeight: 1.55 }}>{data.how}</p>
        {data.parts.map((p) => <PartHow key={p.zoneId} p={p} />)}
        {data.parts.length > 1 && data.figureEgp !== undefined && <p className="num" style={{ fontWeight: 600 }}>Average of the {data.parts.length} zones: {n2(data.figureEgp)} EGP</p>}
      </>}
    </div>
  );
}

function PartHow({ p }: { p: CostPart }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
      <div><b>{p.zoneId}</b>: {KIND[p.kind] ?? p.kind} ({p.assetName}), assumed {p.delayHours} h before the crew finishes. Cost of delay for this zone: <b className="num">{n2(p.zoneCostOfDelayEgp)} EGP</b></div>
      <table className="table" style={{ marginTop: 6 }}>
        <thead><tr><th>Cost line</th><th>Formula</th><th>With these inputs</th><th className="r">EGP</th></tr></thead>
        <tbody>
          {p.ownLines.map((l) => { const k = p.ownSteps.find((x) => Math.abs(x.result - l.amount) <= 0.01); return (
            <tr key={l.label}><td>{l.label}<div className="muted" style={{ fontSize: 11.5 }}>{l.cause}</div></td><td style={{ fontSize: 12 }}>{k?.formula ?? ''}</td>
              <td style={{ fontSize: 12 }}>{k?.inputs.map((i) => `${i.name} = ${n2(i.value)} ${i.unit}`).join(', ')}</td><td className="r num">{n2(l.amount)}</td></tr>); })}
          <tr><td colSpan={3}><b>This zone's part</b></td><td className="r num"><b>{n2(p.zoneCostOfDelayEgp)}</b></td></tr>
        </tbody>
      </table>
      <div className="muted" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>
        The whole incident costs {n2(p.incidentCostOfDelayEgp)} EGP: this zone's part, {n2(p.otherZonesEgp)} EGP for the other zones it reaches, and {n2(p.sharedLines.reduce((a, l) => a + l.amount, 0))} EGP shared by the whole incident
        {p.sharedLines.length > 0 && <> ({p.sharedLines.map((l) => `${l.label.toLowerCase()} ${n2(l.amount)}`).join(', ')})</>}. Shared lines are not added to the zone's figure, so nothing is counted twice.
      </div>
      <details style={{ marginTop: 6 }}>
        <summary style={{ cursor: 'pointer', fontSize: 12.5 }}>Assumptions behind it ({p.assumptions.length}) and the formula of the whole incident</summary>
        {p.totalStep && <div className="num" style={{ fontSize: 12, margin: '6px 0' }}>{p.totalStep.name} = {p.totalStep.formula} = {n2(p.totalStep.result)} {p.totalStep.unit}</div>}
        <table className="table"><tbody>{p.assumptions.map((a) => <tr key={a.key}><td>{a.name}<div className="muted" style={{ fontSize: 11.5 }}>{a.basis}</div></td><td className="r num">{n2(a.value)} <span className="muted">{a.unit}</span></td><td><span className="chip">{a.status}</span></td></tr>)}</tbody></table>
      </details>
    </div>
  );
}

export function CityDecisionCard() {
  const { data, error } = useCityDecision();
  const [open, setOpen] = useState<string | null>(null);
  if (error) return null; // the joint table is an add-on: without it the page is the same as before
  const counts = data ? groupCounts(data) : null;
  return (
    <Card title="Outage cost beside environmental stress" sub="Our figures come from the twin; the environment and the hospitals come from the healthcare team. Each side is compared with the median district; the two are never added together." pad={false} style={{ gridColumn: '1 / -1' }}>
      {!data ? <div className="card-body"><Skeleton h={220} /></div> : <>
        <div className="row wrap" style={{ gap: 8, padding: '12px 16px 4px' }}>
          {GROUPS.filter((g) => counts![g.id] > 0).map((g) => <span key={g.id} className={`chip ${g.tone === 'muted' ? '' : g.tone}`} title={g.meaning}>{g.label}: <b className="num">{counts![g.id]}</b></span>)}
        </div>
        <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
          <table className="table">
            <thead><tr><th>District</th><th className="r">Outage cost of delay (twin)</th><th className="r">Environmental stress (theirs, {data.envMonth})</th><th>Hospitals</th><th>Hospital pressure (theirs)</th><th>Group</th></tr></thead>
            <tbody>
              {sortDecision(data.rows).map((r) => {
                const g = GROUPS.find((x) => x.id === r.group)!;
                return (
                  <Fragment key={r.districtKey}>
                  <tr>
                    <td><span dir="auto">{r.nameAr}</span> <span className="muted">{r.districtCode}</span>{r.matchKind !== 'direct' && <div className="muted" style={{ fontSize: 11.5 }}>{r.matchKind === 'type' ? 'Same kind of district, not the same one' : 'No counterpart in the twin'}</div>}</td>
                    <td className="r num">{r.worstCostOfDelayEgp === undefined ? <span className="muted">–</span> : <>{compact(r.worstCostOfDelayEgp)} <span className="muted">EGP</span><div className="muted" style={{ fontSize: 11.5 }}>{r.outageVsMedian} than median</div>
                      <div className="muted" style={{ fontSize: 11.5 }}>{[['electricity', r.electricityWorstCostEgp], ['water', r.waterWorstCostEgp], ['cooling', r.coolingWorstCostEgp]].filter(([, v]) => v !== undefined).map(([n, v]) => `${n} ${compact(v as number)}`).join(' · ')}</div>
                      <button type="button" className="btn sm" style={{ marginTop: 4 }} aria-expanded={open === r.districtKey} onClick={() => setOpen(open === r.districtKey ? null : r.districtKey)}>{open === r.districtKey ? 'Hide' : 'How is it calculated?'}</button></>}</td>
                    <td className="r num"><span className="row" style={{ gap: 6, justifyContent: 'flex-end' }}><i className="dot" style={{ color: `var(${BAND_VAR[r.esiBand]})` }} /><b>{fmt(r.esiOverall, 1)}</b> <span className="muted">{BAND_LABEL[r.esiBand]}</span></span><div className="muted" style={{ fontSize: 11.5 }}>highest: {r.highestScore} {fmt(r[`esi${r.highestScore[0].toUpperCase()}${r.highestScore.slice(1)}` as 'esiAir'], 1)}</div></td>
                    <td>{r.hospitals} hospital{r.hospitals === 1 ? '' : 's'} <span className="muted">of {r.hospitals + r.clinics + r.specialists} facilities</span><div className="muted" style={{ fontSize: 11.5 }}>{hospitalLine(r)}</div></td>
                    <td>{(() => { const p = pressureLine(r); return p ? <><b className="num" style={{ color: r.overCapacity ? 'var(--warn-text)' : undefined }}>{p.main}</b>{p.sub && <div className="muted" style={{ fontSize: 11.5 }}>{p.sub}</div>}</> : <span className="muted">–</span>; })()}</td>
                    <td><span className={`chip ${g.tone === 'muted' ? '' : g.tone}`} title={g.meaning}>{g.label}</span></td>
                  </tr>
                  {open === r.districtKey && <tr><td colSpan={6} style={{ background: 'var(--hover)' }}><CostHow row={r} /></td></tr>}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="muted" style={{ fontSize: 11.5, padding: '10px 16px 14px', lineHeight: 1.5 }}>
          Cost of delay is the twin's largest figure among the failures that touch the district (median {compact(data.medianCostOfDelayEgp)} EGP); stress is their 0 to 100 score (median {fmt(data.medianEsiOverall, 1)}, bands: under 20 low, 20 to 35 moderate, 35 and over high).
          Hospital pressure is demand over capacity from their what-if engine in its default stress-calibrated view (which they describe as a scenario visualization, not observed utilization), run unchanged with their default settings and that district as the only target (their package costs are placeholders of their own configuration). Hospitals are placed by the district of their file and the district by name and kind, so a count marked "may reach" is an upper bound. Both projects' data are simulated; the cost uses planning assumptions. Source: {data.source}.
        </div>
      </>}
    </Card>
  );
}
