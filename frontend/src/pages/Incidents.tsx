import { useEffect, useMemo, useState } from 'react';
import { Bot, Download, FlaskConical, MessageCircle, ShieldCheck } from 'lucide-react';
import { post } from '../api/client';
import { useFetch } from '../api/dash';
import type { Localization } from '../api/lab';
import type { AgentFinding, Alert, Incident } from '../api/types';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { CityMap, type Focus } from '../map/CityMap';
import { TimeChart } from '../charts/TimeChart';
import { Card, Empty, NetIcon, SeverityChip, Skeleton, StatusChip } from '../ui';
import { CalcView, RangeBar, Reasons } from '../ui/Calc';
import { CostTiles } from '../ui/parts';
import { DecisionStudio, WhereCard } from '../ui/Decision';
import { BoardPanel } from '../ui/Board';
import { go, href } from '../lib/route';
import { assetBounds, money, useSensorSeries } from '../lib/useSensorSeries';
import { ago, clock, fmt, sensorLabel } from '../lib/format';
import { latestFinding } from '../lib/present';

/** Reveals the agent's real steps one by one when the finding is fresh; shows them all when it is old. */
function Steps({ finding }: { finding: AgentFinding }) {
  const fresh = Date.now() - Date.parse(finding.createdAt) < 25_000;
  const [n, setN] = useState(fresh ? 0 : finding.reasoningSteps.length);
  useEffect(() => {
    if (n >= finding.reasoningSteps.length) return;
    const t = setTimeout(() => setN((x) => x + 1), 420);
    return () => clearTimeout(t);
  }, [n, finding.reasoningSteps.length]);
  return (
    <ol style={{ margin: 0, paddingInlineStart: 18, display: 'flex', flexDirection: 'column', gap: 4, color: 'var(--text-2)', fontSize: 13 }}>
      {finding.reasoningSteps.slice(0, n).map((s, i) => <li key={i} style={{ animation: 'fade var(--t-med) var(--ease)' }}>{s}</li>)}
      {n < finding.reasoningSteps.length && <li className="muted">...</li>}
    </ol>
  );
}

function Step({ n, title, right, children }: { n: number; title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card pad={false}>
      <div className="card-body">
        <div className="row" style={{ gap: 10, marginBottom: 10 }}>
          <span className="num" style={{ width: 24, height: 24, borderRadius: 12, background: 'var(--accent-soft)', color: 'var(--accent)', display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 600 }}>{n}</span>
          <span className="card-title">{title}</span><span style={{ marginInlineStart: 'auto' }}>{right}</span>
        </div>
        {children}
      </div>
    </Card>
  );
}

function IncidentView({ incident, finding, alerts }: { incident: Incident; finding?: AgentFinding; alerts: Alert[] }) {
  const topology = useStore((s) => s.topology.data);
  const set = useUi((s) => s.set); const notify = useUi((s) => s.notify);
  const [busy, setBusy] = useState(false);
  const zone = topology?.zones.find((z) => z.id === incident.zoneIds[0]);
  const bounds = assetBounds(topology, alerts.flatMap((a) => a.assetIds));
  const [picked, setPicked] = useState<{ assetId: string; key: number } | null>(null);
  const isBurst = /burst|leak/i.test(incident.title);
  const loc = useFetch<Localization>(isBurst ? `/incidents/${incident.id}/localization` : null, 6000);
  const suspects = loc.data?.candidates.map((c) => ({ assetId: c.assetId, rank: c.rank }));
  const pickedBounds = picked ? assetBounds(topology, [picked.assetId]) : null;
  const focus: Focus | null = pickedBounds ? { lng: 0, lat: 0, bounds: pickedBounds, key: 1000 + picked!.key }
    : zone ? { lng: zone.center.lng, lat: zone.center.lat, zoom: 14, bounds: bounds ?? undefined, key: 1 } : null;

  // The sensor that triggered the finding: first measured evidence that is a real sensor.
  const sensorId = useMemo(() => {
    const ids = new Set(topology?.sensors.map((s) => s.id));
    const ref = finding?.explain.reasons.find((r) => r.evidenceType === 'measured' && ids.has(r.evidenceRef))?.evidenceRef;
    if (ref) return ref;
    const asset = alerts[0]?.assetIds[0];
    return topology?.sensors.find((s) => s.assetId === asset)?.id;
  }, [topology, finding, alerts]);
  const sensor = topology?.sensors.find((s) => s.id === sensorId);
  const series = useSensorSeries(sensorId, 10);

  const open = alerts.filter((a) => a.state === 'open');
  const ack = async () => {
    setBusy(true);
    try { await Promise.all(open.map((a) => post(`/alerts/${a.id}/acknowledge`))); notify('Incident acknowledged.'); }
    catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  };
  const timeline = [
    ...alerts.map((a) => ({ t: a.detectedAt, text: `Alert raised: ${a.title}`, who: `${Math.round(a.confidence * 100)}% confidence` })),
    ...(finding ? [{ t: finding.createdAt, text: `${finding.agent[0].toUpperCase() + finding.agent.slice(1)} agent finished its analysis`, who: finding.numberCheck === 'passed' ? 'every figure verified' : 'figures corrected' }] : []),
  ].sort((a, b) => a.t.localeCompare(b.t));

  return (
    <>
      <div className="page-head">
        <div>
          <div className="row wrap" style={{ gap: 8, marginBottom: 6 }}>
            <SeverityChip s={incident.severity} /><StatusChip s={incident.state === 'resolved' ? 'resolved' : 'open'} label={incident.state === 'acknowledged' ? 'Acknowledged' : undefined} />
            {incident.networks.map((n) => <span key={n} className={`chip ${n}`}><NetIcon n={n} size={13} />{n}</span>)}
            <span className="muted num">{incident.id}</span>
          </div>
          <h1>{incident.title}</h1>
          <p>{zone?.name ?? incident.zoneIds.join(', ')}. Opened {ago(incident.openedAt)} ago.</p>
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          <button className="btn" onClick={() => set({ chatOpen: true })}><MessageCircle size={16} />Ask the team</button>
          <a className="btn" href={`/api/v1/incidents/${incident.id}/report`} download><Download size={16} />Report (PDF)</a>
          <button className="btn primary" disabled={!open.length || busy} onClick={ack}><ShieldCheck size={16} />{open.length ? 'Acknowledge' : 'Acknowledged'}</button>
        </div>
      </div>

      <div className="inc-grid">
        <section className="stack" style={{ gap: 16, minWidth: 0 }}>
          <CityMap height={400} focus={focus} suspects={suspects} showLegend={false} onSelectAsset={(id) => set({ selectedAsset: id })} />
          <BoardPanel key={incident.id} incidentId={incident.id} />
          <Card title={sensor ? `${sensorLabel(sensor.id.replace(sensor.assetId + '-', '')).replace(/^./, (c) => c.toUpperCase())} on ${topology?.assets.find((a) => a.id === sensor.assetId)?.name}` : 'Key sensor'}
            sub="Measured, against what the twin expects in normal operation">
            {sensor && series.measured.length > 3 ? (
              <TimeChart label="Key sensor, measured and expected" height={220} unit={sensor.unit} digits={2}
                xTick={(t) => clock(t, false)}
                series={[{ id: 'm', label: 'Measured', color: 'var(--crit)', points: series.measured },
                  { id: 'e', label: 'Twin expects', color: 'var(--text-3)', dash: true, width: 1.6, points: series.expected }]}
                thresholds={[{ v: sensor.normalRange.min, label: 'normal minimum', color: 'var(--border-strong)' }]} />
            ) : <Skeleton h={240} />}
          </Card>
          <Card title="Timeline">
            <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {timeline.map((e, i) => (
                <li key={i} style={{ display: 'grid', gridTemplateColumns: '70px 12px 1fr', gap: 10, alignItems: 'start' }}>
                  <span className="num muted">{clock(e.t)}</span><i className="dot" style={{ color: i === timeline.length - 1 ? 'var(--accent)' : 'var(--text-3)', marginTop: 6 }} />
                  <span>{e.text} <span className="muted">· {e.who}</span></span>
                </li>
              ))}
              {incident.state !== 'resolved' && <li style={{ display: 'grid', gridTemplateColumns: '70px 12px 1fr', gap: 10 }}><span className="num muted">now</span><i className="dot" style={{ color: 'var(--warn)', marginTop: 6 }} /><span className="sub">{open.length ? 'Waiting for someone to acknowledge' : 'Acknowledged. The incident resolves when the readings recover.'}</span></li>}
            </ol>
          </Card>
        </section>

        <aside className="stack" style={{ gap: 16, minWidth: 0 }}>
          {incident.reanalysing && finding && <Card><div className="row" style={{ gap: 8 }}><Bot size={18} /><span><b>The incident grew.</b> The agents are re-analysing it, so the steps below are from the earlier stage and will be replaced.</span></div></Card>}
          {!finding ? <Card><div className="stack"><div className="row" style={{ gap: 8 }}><Bot size={18} /><b>The agents are analysing this incident</b></div><Skeleton h={14} /><Skeleton h={14} w="80%" /><Skeleton h={90} /></div></Card> : <>
            <Step n={1} title="What happened" right={<span className="row" style={{ gap: 6 }}><span className="chip"><NetIcon n={finding.agent === 'orchestrator' ? 'water' : finding.agent} size={13} />{finding.agent} agent</span>{finding.fallback && <span className="chip warn">Recorded answer</span>}</span>}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{finding.headline}</div>
              <p className="sub" style={{ margin: '0 0 10px' }}>{money(finding.explanation)}</p>
              <details><summary className="sub" style={{ cursor: 'pointer' }}>What the agent did</summary><div style={{ marginTop: 8 }}><Steps finding={finding} /></div></details>
            </Step>
            <Step n={2} title="Why we are sure" right={<span className="num muted">confidence {fmt(finding.explain.confidence * 100)}%</span>}>
              <Reasons reasons={finding.explain.reasons} />
              {finding.probableCauses.length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>Probable causes: {finding.probableCauses.join('; ')}</div>}
            </Step>
            <Step n={3} title="What it costs" right={<button className="btn ghost sm" onClick={() => set({ calcId: finding.explain.calculationId })}>{finding.explain.calculationId}</button>}>
              <CostTiles finding={finding} />
              {finding.cost.savingRange && <div style={{ marginTop: 14 }}><RangeBar {...finding.cost.savingRange} /></div>}
              <details style={{ marginTop: 12 }}><summary className="sub" style={{ cursor: 'pointer' }}>How the numbers are calculated</summary><div style={{ marginTop: 12 }}><CalcView cost={finding.cost} explanation={finding.explain} /></div></details>
            </Step>
            {isBurst && <WhereCard data={loc.data} onPick={(assetId) => setPicked({ assetId, key: Date.now() })} />}
            <DecisionStudio incidentId={incident.id} />
            <Step n={4} title="What to do" right={<span className={`chip ${finding.recommendedAction.urgency === 'immediate' ? 'crit' : 'warn'}`}>{finding.recommendedAction.urgency.replace('_', ' ')}</span>}>
              <p style={{ margin: '0 0 10px', lineHeight: 1.5 }}>{money(finding.recommendedAction.text)}</p>
              <div className="row wrap" style={{ gap: 8 }}><span className="chip">Crew: {finding.recommendedAction.crewType}</span>{finding.predictedImpact.zoneIds.length > 0 && <span className="chip">Affects {finding.predictedImpact.zoneIds.length} district{finding.predictedImpact.zoneIds.length > 1 ? 's' : ''}</span>}</div>
              {finding.predictedImpact.text && <div className="sub" style={{ marginTop: 8, fontSize: 13 }}>{money(finding.predictedImpact.text)}</div>}
            </Step>
          </>}
        </aside>
      </div>
    </>
  );
}

export function Incidents({ id }: { id?: string }) {
  const incidents = useStore((s) => s.incidents);
  const findings = useStore((s) => s.findings.data);
  const alerts = useStore((s) => s.alerts.data);
  const set = useUi((s) => s.set);
  const sorted = [...incidents].sort((a, b) => b.openedAt.localeCompare(a.openedAt));
  const current = sorted.find((i) => i.id === id) ?? sorted.find((i) => i.state !== 'resolved') ?? sorted[0];
  if (!current) {
    return (
      <div className="page">
        <div className="page-head"><div><h1>Incidents</h1><p>Every incident groups its alerts, the agent's reasons, the cost of acting and the cost of waiting.</p></div></div>
        <Card><Empty title="No incidents yet">The networks are within normal range. Start a scenario to see how the team responds.
          <div style={{ marginTop: 14 }}><button className="btn primary" onClick={() => set({ scenarioOpen: true })}><FlaskConical size={16} />Open the scenario lab</button></div></Empty></Card>
      </div>
    );
  }
  return (
    <div className="page">
      {sorted.length > 1 && (
        <div className="row wrap" style={{ gap: 8 }} role="tablist" aria-label="Incidents">
          {sorted.map((i) => <a key={i.id} role="tab" aria-selected={i.id === current.id} href={href('incidents', i.id)} className={`chip ${i.id === current.id ? 'accent' : ''}`} style={{ height: 28, textDecoration: 'none' }} onClick={() => go('incidents', i.id)}>{i.id} · {i.title}</a>)}
        </div>
      )}
      <IncidentView key={current.id} incident={current} finding={latestFinding(findings, current.id)} alerts={alerts.filter((a) => a.incidentId === current.id)} />
    </div>
  );
}
