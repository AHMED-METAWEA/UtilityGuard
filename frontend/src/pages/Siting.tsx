import { useEffect, useState } from 'react';
import { Building2, CircleAlert, CircleCheck, Play } from 'lucide-react';
import { evaluateSiting, useSitingOptions, type ConditionResult, type FacilityKind, type NetworkRisk, type SitingResult } from '../api/siting';
import { useUi } from '../store/ui';
import { Card, Empty, NetIcon, Skeleton } from '../ui';
import { compact, fmt, pct } from '../lib/format';

const Scroll = ({ children }: { children: React.ReactNode }) => <div style={{ overflowX: 'auto', maxWidth: '100%' }}>{children}</div>;

const NET_LABEL = { electricity: 'Electricity', water: 'Water', cooling: 'Cooling' } as const;
const MATCH = { direct: 'the same district in both projects', type: 'matched by kind of district, read as "a district like this"', none: 'no counterpart' } as const;
const delta = (r: NetworkRisk) => r.probabilityAfter - r.probabilityBefore;
const tone = (d: number) => (d >= 0.1 ? 'crit' : d > 0 ? 'warn' : 'ok');

function Share({ before, after }: { before: number; after: number }) {
  return <span className="num"><span className="muted">{pct(before)}</span> → <b>{pct(after)}</b></span>;
}

function Condition({ c }: { c: ConditionResult }) {
  return (
    <div>
      <div className="row spread wrap" style={{ marginBottom: 6 }}>
        <b>{c.label}</b>
        <span className="muted" style={{ fontSize: 12 }}>{fmt(c.days)} simulated days · any breach <Share before={c.anyBefore} after={c.anyAfter} /></span>
      </div>
      <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
        <table className="table">
          <thead><tr><th>Network</th><th className="r">Days in breach</th><th className="r">Days at warning level</th><th className="r">Busiest asset (95th percentile)</th><th>Limiting asset</th></tr></thead>
          <tbody>
            {c.risks.map((r) => (
              <tr key={r.network}>
                <td><span className="row" style={{ gap: 6 }}><NetIcon n={r.network} size={15} />{NET_LABEL[r.network]}</span><div className="muted" style={{ fontSize: 11.5, maxWidth: 360 }}>{r.limit}</div></td>
                <td className="r"><span className={`chip ${tone(delta(r))}`}><Share before={r.probabilityBefore} after={r.probabilityAfter} /></span></td>
                <td className="r"><Share before={r.watchBefore} after={r.watchAfter} /></td>
                <td className="r num">{r.utilisationAfterP95 != null && r.utilisationBeforeP95 != null ? <><span className="muted">{pct(r.utilisationBeforeP95)}</span> → <b>{pct(r.utilisationAfterP95)}</b></> : <span className="muted">n/a</span>}</td>
                <td>{r.asset ?? <span className="muted">none</span>}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
      {c.extraUnservedMwhPerDay > 0.005 && (
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          On a day like this the facility adds about <b className="num" style={{ color: 'var(--text)' }}>{fmt(c.extraUnservedMwhPerDay, 1)} MWh</b> of electricity above the firm capacity, worth about <b className="num" style={{ color: 'var(--text)' }}>{compact(c.extraUnservedCostEgpPerDay)} EGP</b> at the value of lost load (range {compact(c.extraUnservedCostLowEgpPerDay)} to {compact(c.extraUnservedCostHighEgpPerDay)}).
        </div>)}
    </div>
  );
}

function Result({ r }: { r: SitingResult }) {
  const worst = Math.max(...r.conditions.flatMap((c) => c.risks.map(delta)));
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Card>
        <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
          <span className={`chip ${tone(worst)}`}>{worst >= 0.1 ? <CircleAlert size={13} /> : <CircleCheck size={13} />}{worst >= 0.1 ? 'Needs utility upgrades' : 'The networks take it'}</span>
          <div style={{ fontSize: 15, lineHeight: 1.5 }}>{r.verdict}</div>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
          Load added to {r.zone.name}: <b className="num">{fmt(r.load.waterM3H, 1)}</b> m3/h water, <b className="num">{fmt(r.load.electricityMw, 2)}</b> MW electricity, <b className="num">{fmt(r.load.coolingMw, 2)}</b> MW cooling
          {r.load.onSiteChillersMw > 0 && <> (this zone has no district cooling, so its own chillers add <b className="num">{fmt(r.load.onSiteChillersMw, 2)}</b> MW to the electricity)</>}. Fed by {r.zone.substation}{r.zone.coolingPlant ? `, ${r.zone.coolingPlant}` : ''}, pump {r.zone.pump}.
        </div>
      </Card>
      <Card title="What it does to the networks" sub={`${r.runs} draws of the facility's load inside its ranges, each on a different summer weekday. A share of days is a probability under these assumptions, not a forecast of the real network.`}>
        <div className="stack" style={{ gap: 22 }}>{r.conditions.map((c) => <Condition key={c.condition} c={c} />)}</div>
      </Card>
      <Card title="What it costs" sub="The facility itself plus the utility upgrades the simulation says it needs">
        <Scroll><table className="table">
          <tbody>
            <tr><td>Building the facility<div className="muted" style={{ fontSize: 11.5 }}>{r.cost.capexBasis}</div></td><td className="r num">{r.cost.capexMEgp != null ? `${fmt(r.cost.capexMEgp, 1)} M EGP` : <span className="muted">not priced</span>}</td></tr>
            {r.cost.mitigations.map((m) => <tr key={m.network}><td>{m.package} ({m.assets.join(', ')})<div className="muted" style={{ fontSize: 11.5 }}>{m.reason}</div></td><td className="r num">{fmt(m.costMEgp, 1)} M EGP</td></tr>)}
            {!r.cost.mitigations.length && <tr><td className="muted">No utility upgrade needed</td><td className="r num">0 M EGP</td></tr>}
            <tr><td><b>Total</b></td><td className="r num"><b>{r.cost.totalMEgp != null ? `${fmt(r.cost.totalMEgp, 1)} M EGP` : 'enter the facility cost'}</b></td></tr>
          </tbody>
        </table></Scroll>
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>{r.cost.note}</div>
      </Card>
      {r.districts.length > 0 && (
        <Card title="The healthcare project's districts in this zone" sub="Their figures, side by side with the above, never added to them">
          <Scroll><table className="table">
            <thead><tr><th>District</th><th>Match</th><th className="r">Hospitals</th><th className="r">Hospital pressure (theirs)</th><th className="r">Environmental stress (theirs)</th></tr></thead>
            <tbody>{r.districts.map((d) => (
              <tr key={d.districtKey}><td>{d.districtCode}</td><td style={{ fontSize: 12 }}>{MATCH[d.matchKind]}</td><td className="r num">{d.hospitals}</td>
                <td className="r num">{d.theirPressureBaseline != null ? <>{fmt(d.theirPressureBaseline, 2)} {d.theirOverCapacity && <span className="chip warn">over capacity</span>}</> : '—'}</td>
                <td className="r num">{d.esiOverall != null ? fmt(d.esiOverall, 1) : '—'}</td></tr>))}</tbody>
          </table></Scroll>
          <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>Only the districts marked "the same district" are this zone. The others are matched by kind, so read them as "a district like this".</div>
        </Card>)}
      <Card title="The assumptions behind the load">
        <Scroll><table className="table"><tbody>{r.assumptions.map((a) => <tr key={a.name}><td>{a.name}<div className="muted" style={{ fontSize: 11.5 }}>{a.basis}</div></td>
          <td className="r num">{fmt(a.value, 3)} <span className="muted">{a.unit}</span><div className="muted" style={{ fontSize: 11.5 }}>range {fmt(a.low, 3)} to {fmt(a.high, 3)}</div></td></tr>)}</tbody></table></Scroll>
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>The twin is simulated. The load of a facility is a planning allowance, and the limits are the twin's own (firm capacity of a substation 110 MW, the top of a plant's normal range, a pump's set-point). Replace the loads with the design brief's schedule before using a number outside this demonstration.</div>
      </Card>
    </div>
  );
}

export function Siting() {
  const options = useSitingOptions();
  const notify = useUi((s) => s.notify);
  const [zone, setZone] = useState('');
  const [kind, setKind] = useState<FacilityKind>('hospital');
  const [units, setUnits] = useState(300);
  const [custom, setCustom] = useState({ water: '12', electricity: '2.4', cooling: '3' });
  const [capex, setCapex] = useState('');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<SitingResult | null>(null);
  const o = options.data;
  useEffect(() => { if (o && !zone) setZone(o.zones.find((z) => z.id === 'zone-gov')?.id ?? o.zones[0].id); }, [o, zone]);
  const k = o?.kinds.find((x) => x.id === kind);
  const pick = (v: FacilityKind) => { setKind(v); const n = o?.kinds.find((x) => x.id === v)?.defaultUnits; if (n) setUnits(n); setRes(null); };

  const run = async () => {
    setBusy(true);
    try {
      const num = (s: string) => (s.trim() === '' ? undefined : Number(s));
      setRes(await evaluateSiting({
        zoneId: zone, kind, units, capexMEgp: num(capex),
        ...(kind === 'custom' ? { waterM3H: num(custom.water) ?? 0, electricityMw: num(custom.electricity) ?? 0, coolingMw: num(custom.cooling) ?? 0 } : {}),
      }));
    } catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Build here</h1><p>Put a hospital, an expansion or a clinic in a zone and see what it does to the water, electricity and cooling networks, and what it costs. It runs on a separate copy of the twin; the live twin is not touched.</p></div>
      </div>
      <div className="inc-grid" style={{ ['--cols' as string]: 'minmax(280px, 360px) minmax(0, 1fr)', alignItems: 'start' }}>
        <Card title={<span className="row" style={{ gap: 8 }}><Building2 size={16} />The facility</span>}>
          {!o ? <Skeleton h={260} /> : (
            <div className="stack" style={{ gap: 14 }}>
              <label className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>Where</span>
                <select className="field" value={zone} onChange={(e) => { setZone(e.target.value); setRes(null); }}>
                  {o.zones.map((z) => <option key={z.id} value={z.id}>{z.name}{z.coolingPlant ? '' : ' (no district cooling)'}</option>)}
                </select></label>
              <label className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>What</span>
                <select className="field" value={kind} onChange={(e) => pick(e.target.value as FacilityKind)}>
                  {o.kinds.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                </select></label>
              <label className="stack" style={{ gap: 4 }}><span className="row spread sub" style={{ fontSize: 12 }}><span>How big ({k?.unit})</span><b className="num" style={{ color: 'var(--text)' }}>{fmt(units)}</b></span>
                <input type="range" min={1} max={kind === 'clinic' || kind === 'custom' ? 40 : 1500} step={1} value={units} onChange={(e) => { setUnits(Number(e.target.value)); setRes(null); }} aria-label="Size of the facility" /></label>
              {kind === 'custom' && (
                <div className="stack" style={{ gap: 8 }}>
                  {([['water', 'Water per facility (m3/h)'], ['electricity', 'Electricity per facility (MW)'], ['cooling', 'Cooling per facility (MW thermal)']] as const).map(([key, label]) => (
                    <label key={key} className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>{label}</span>
                      <input className="field" type="number" min={0} step="any" value={custom[key]} onChange={(e) => { setCustom({ ...custom, [key]: e.target.value }); setRes(null); }} /></label>))}
                </div>)}
              <label className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>Cost of building it, M EGP (optional)</span>
                <input className="field" type="number" min={0} step="any" placeholder={k?.capexMEgp != null ? `${k.capexMEgp} (the healthcare team's placeholder)` : 'not priced'} value={capex} onChange={(e) => { setCapex(e.target.value); setRes(null); }} /></label>
              <button className="btn primary" onClick={run} disabled={busy || !zone}><Play size={16} />{busy ? 'Simulating...' : 'Simulate'}</button>
            </div>)}
        </Card>
        <div style={{ minWidth: 0 }}>
          {res ? <Result r={res} /> : <Card><Empty title={busy ? 'Simulating' : 'Nothing simulated yet'}>{busy ? 'Reading the networks on hundreds of summer days, with and without the facility.' : 'Pick a zone and a facility, then press Simulate.'}</Empty></Card>}
        </div>
      </div>
    </div>
  );
}
