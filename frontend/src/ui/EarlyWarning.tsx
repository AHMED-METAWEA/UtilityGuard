import { CircleCheck, Clock, ExternalLink, TriangleAlert } from 'lucide-react';
import type { EarlyWarning } from '../api/types';
import { TimeChart } from '../charts/TimeChart';
import { fmt } from '../lib/format';
import { warningTitle } from '../lib/warning';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { Card, Empty, NetIcon } from './index';
import { EvidenceTag } from './Provenance';
import './EarlyWarning.css';

export function EarlyWarningCard({ warning }: { warning: EarlyWarning }) {
  const { cost } = warning;
  const points = warning.series.map((p) => ({ t: Date.parse(p.t), v: p.risk * 100 }));
  return <Card className="early-warning" title={<span className="row" style={{ gap: 8 }}><TriangleAlert size={17} color="var(--warn-text)" aria-hidden />{warningTitle(warning)}</span>}
    sub={<span className="row wrap" style={{ gap: 6 }}><NetIcon n={warning.network} size={13} />{warning.assetName} · {warning.state}<EvidenceTag e="model" /></span>}>
    <div className="warning-grid">
      <div className="stack" style={{ gap: 8, minWidth: 0 }}>
        <div className="row wrap" style={{ gap: 8 }}><b className="num">Risk score {fmt(warning.risk * 100, 1)}%</b><span className="muted">Peer score {fmt(warning.peerRisk * 100, 1)}%</span></div>
        {points.length > 0 && <TimeChart label={`${warning.assetName} risk score versus what age and load explain`} height={170} yDomain={[0, 100]} yTick={(v) => `${fmt(v)}%`} unit="%" digits={1}
          series={[{ id: 'risk', label: 'Risk score', color: 'var(--warn-text)', points },
            { id: 'peer', label: 'what age and load explain', color: 'var(--text-3)', dash: true, points: warning.series.map((p) => ({ t: Date.parse(p.t), v: p.peer * 100 })) }]}
          band={{ label: 'Gap from peers', color: 'var(--warn)', points: warning.series.map((p) => ({ t: Date.parse(p.t), lo: Math.min(p.risk, p.peer) * 100, hi: Math.max(p.risk, p.peer) * 100 })) }} />}
        <div className="warning-signals">{warning.signals.map((s) => <span className="chip" key={s.label}>{s.label}: <b className="num">{fmt(s.value, 2)} vs peers {fmt(s.peerValue, 2)}</b> {s.unit}</span>)}</div>
      </div>
      <div className="stack" style={{ gap: 10 }}>
        <div className="row wrap" style={{ gap: 6 }}><span className="sub">Maintenance economics</span><EvidenceTag e="calculated" /><EvidenceTag e="assumed" /></div>
        <dl className="warning-costs">
          <div><dt>If it fails</dt><dd>{fmt(cost.unplanned)} <small>EGP</small></dd></div>
          <div><dt>Planned maintenance</dt><dd>{fmt(cost.planned)} <small>EGP</small></dd></div>
          <div><dt>Maintenance pays above</dt><dd>{fmt(cost.breakEven * 100, 1)}%</dd></div>
        </dl>
        <div className="row wrap" style={{ gap: 8 }}><span className="chip warn">{cost.verdict === 'maintain_now' ? <TriangleAlert size={13} aria-hidden /> : <Clock size={13} aria-hidden />}{cost.verdict === 'maintain_now' ? 'Maintain now' : 'Keep watching'}</span><span className="num">Expected saving {fmt(cost.expectedSaving, 2)} EGP</span></div>
      </div>
    </div>
    <p className="warning-text">{warning.text}</p>
    <p className="warning-limit sub">The risk score is a ranking, not a calibrated probability of failure. The model is trained on a synthetic hazard; this demonstrates the mechanism, not field accuracy. The maintenance calculation uses that score as an assumption.</p>
    {warning.minutesTo90 != null && (warning.risePerMinute ?? 0) > 0 && <p className="warning-limit muted">If the trend continues the score reaches 90% in about {fmt(warning.minutesTo90, 1)} min (an extrapolation, not a forecast)</p>}
    <div className="row wrap" style={{ gap: 8 }}>
      <button className="btn sm" onClick={() => useUi.getState().set({ whyRef: warning.whyRef })}>Why this?</button>
      <button className="btn sm" onClick={() => useUi.getState().set({ selectedAsset: warning.assetId })}><ExternalLink size={13} aria-hidden />Open asset</button>
    </div>
  </Card>;
}

export function ActiveWarnings() {
  const warnings = useStore((s) => s.warnings);
  const active = Object.values(warnings).filter((warning) => warning.state === 'active');
  if (!active.length) return null;
  return <section className="stack" aria-label="Active early warnings">
    {active.slice(0, 2).map((warning) => <EarlyWarningCard key={warning.id} warning={warning} />)}
    {active.length > 2 && <details className="warning-more"><summary>Show {active.length - 2} more early warning{active.length === 3 ? '' : 's'}</summary><div className="stack">{active.slice(2).map((warning) => <EarlyWarningCard key={warning.id} warning={warning} />)}</div></details>}
  </section>;
}

export function EarlyWarningsOverview() {
  const warnings = useStore((s) => s.warnings);
  const list = Object.values(warnings);
  return <Card title="Early warnings" sub="Condition drift while assets are running · active and past warnings" className="early-warning">
    {list.length ? <ul className="warning-overview">{list.map((warning) => <li key={warning.id}>
      <span className={`chip ${warning.state === 'cleared' ? 'ok' : 'warn'}`}>{warning.state === 'cleared' ? <CircleCheck size={13} aria-hidden /> : <TriangleAlert size={13} aria-hidden />}{warning.state}</span>
      <div><b>{warningTitle(warning)}</b>{warning.state === 'cleared' && <span className="sub"> · {warning.assetName}</span>}
        {warning.leadTimeSeconds != null && <div className="muted num">Measured lead time {fmt(warning.leadTimeSeconds / 60, 1)} min</div>}</div>
      <button className="btn ghost sm" onClick={() => useUi.getState().set({ whyRef: warning.whyRef })}>Why this?</button>
    </li>)}</ul> : <Empty title="No early warnings this run">Warnings appear when an asset’s condition drifts from its peers.</Empty>}
  </Card>;
}
