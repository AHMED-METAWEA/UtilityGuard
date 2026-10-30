import { useMemo, useState } from 'react';
import { CircleAlert, CircleCheck, FlaskConical, Play, Radar, Trophy } from 'lucide-react';
import { useFetch } from '../api/dash';
import { post } from '../api/client';
import type { DetectorId, DetectorScore, LabBenchmark, LabLocalization, LabQuality, QualityNow, SensorValue, TrialResult } from '../api/lab';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { LeakCurve } from '../charts/LeakCurve';
import { Card, Empty, Meter, Skeleton } from '../ui';
import { fmt } from '../lib/format';

const COLOR: Record<DetectorId, string> = { d1: 'var(--d1)', d2: 'var(--d2)', d3: 'var(--d3)', d4: 'var(--d4)' };
const KIND: Record<DetectorScore['kind'], string> = { baseline: 'Baseline', statistical: 'Statistics', unsupervised: 'Unsupervised ML', supervised: 'Supervised ML' };
const min = (v?: number | null) => (v == null ? 'missed' : `${fmt(v)} min`);
const sensorName = (id: string) => id.replace(/^(water|elec|cool)-/, '').replace(/-/g, ' ');

function Swatch({ c }: { c: string }) { return <i className="swatch" style={{ background: c }} />; }

function Benchmark({ b }: { b: LabBenchmark }) {
  const xs = b.leakCurve.map((p) => p.leakM3H);
  const series = b.detectors.map((d) => ({
    id: d.id, label: d.name, color: COLOR[d.id], dash: d.id === 'd1',
    values: b.leakCurve.map((p) => { const r = p.results.find((x) => x.detectorId === d.id)!; return { delay: r.medianDelayMin ?? null, rate: r.detectionRate }; }),
  }));
  const kinds = b.faults.map((f) => f.kind);
  return (
    <>
      <div className="inc-grid" style={{ ['--cols' as string]: 'minmax(0, 1.5fr) minmax(320px, 1fr)' }}>
        <Card title="Which detector finds faults soonest, and what does waiting cost?" sub={`${b.config.testRuns} faults the models never saw, ${fmt(b.config.cleanHours)} hours of normal operation, one reading a minute`} pad={false}>
          <div style={{ overflowX: 'auto' }}>
            <table className="table" style={{ minWidth: 640 }}>
              <thead><tr><th>Detector</th><th className="r">Faults found</th><th className="r">Typical delay</th><th className="r">False alarms / 100 h</th><th className="r">Cost of delay per fault</th></tr></thead>
              <tbody>
                {b.detectors.map((d) => (
                  <tr key={d.id} style={d.id === b.bestDetectorId ? { background: 'var(--accent-soft)' } : undefined}>
                    <td><div className="row" style={{ gap: 8, fontWeight: 600 }}><Swatch c={COLOR[d.id]} />{d.name}{d.id === b.bestDetectorId && <span className="chip accent"><Trophy size={12} />Best</span>}</div>
                      <div className="muted" style={{ fontSize: 12, marginInlineStart: 18 }}><span className="chip" style={{ height: 18, marginInlineEnd: 6 }}>{KIND[d.kind]}</span>{d.note}</div></td>
                    <td className="r num" style={{ whiteSpace: 'nowrap' }}><b>{fmt(d.detectionRate * 100)}%</b>{d.unseenDetectionRate != null && d.kind === 'supervised' && <div className="muted" style={{ fontSize: 11.5 }}>{fmt(d.unseenDetectionRate * 100)}% unseen sites</div>}</td>
                    <td className="r num"><b>{min(d.medianDelayMin)}</b><div className="muted" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>p90 {min(d.p90DelayMin)}</div></td>
                    <td className="r num">{fmt(d.falseAlarmsPer100H, 1)}</td>
                    <td className="r num"><b>{fmt(d.meanDelayCost)}</b> <span className="muted">EGP</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: 0, padding: '10px 16px 14px' }}>Cost of delay is the cost model&rsquo;s hourly cost of each fault at today&rsquo;s prices times the minutes before the alarm. A fault a detector never raised counts as the whole {b.config.windowMinutes}-minute window. Change a price and these figures move.</p>
        </Card>
        <Card title="How small a leak can each one catch?" sub="Median minutes to detect a pipe burst. A cross means it was never found; a hollow dot means some runs were missed.">
          <LeakCurve xs={xs} series={series} label="Detection delay against leak size" xUnit="m3/h" cap={b.config.windowMinutes} />
          <div className="legend" style={{ marginTop: 6 }}>{b.detectors.map((d) => <span key={d.id}><i className="swatch" style={{ background: COLOR[d.id] }} />{d.name}</span>)}</div>
        </Card>
      </div>
      <Card title="By kind of fault" sub="Median minutes to the first alarm" pad={false}>
        <div style={{ overflowX: 'auto' }}>
          <table className="table" style={{ minWidth: 560 }}>
            <thead><tr><th>Fault</th><th className="r">Hourly cost of not knowing</th>{b.detectors.map((d) => <th key={d.id} className="r"><span className="row" style={{ gap: 6, justifyContent: 'flex-end' }}><Swatch c={COLOR[d.id]} />{d.id.toUpperCase()}</span></th>)}</tr></thead>
            <tbody>
              {kinds.map((k) => {
                const f = b.faults.find((x) => x.kind === k)!;
                return (
                  <tr key={k}><td>{f.label}<span className="muted"> · {f.runs} runs</span></td><td className="r num">{fmt(f.hourlyCost)} EGP</td>
                    {b.detectors.map((d) => { const s = d.byFault.find((x) => x.kind === k); return <td key={d.id} className="r num">{s ? <>{min(s.medianDelayMin)}{s.detectionRate < 1 && <span className="muted"> · {fmt(s.detectionRate * 100)}%</span>}</> : '–'}</td>; })}</tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Trial() {
  const topology = useStore((s) => s.topology.data);
  const pipes = useMemo(() => topology?.assets.filter((a) => a.kind === 'pipe') ?? [], [topology]);
  const [pipe, setPipe] = useState('');
  const [size, setSize] = useState(90);
  const [outage, setOutage] = useState<'none' | 'pipe' | 'flows'>('none');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<TrialResult | null>(null);
  const notify = useUi((s) => s.notify);

  const run = async () => {
    setBusy(true);
    try {
      const target = pipe || undefined;
      const disabled = !topology ? [] : outage === 'none' ? [] : topology.sensors.filter((s) => (outage === 'pipe' ? target && s.assetId === target : s.kind === 'flow' && s.assetId.includes('pipe'))).map((s) => s.id);
      setRes(await post<TrialResult>('/lab/trial', { pipeId: target, leakM3H: size, disabledSensors: disabled }));
    } catch (e) { notify((e as Error).message); } finally { setBusy(false); }
  };
  const hit = res?.hitRank === 1;
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><FlaskConical size={16} />Hide a leak and see if the system finds it</span>} sub="A burst is injected into a copy of the twin. The detectors and the locator only see sensor readings.">
      <div className="inc-grid" style={{ ['--cols' as string]: 'minmax(260px, 340px) minmax(0, 1fr)', alignItems: 'start' }}>
        <div className="stack" style={{ gap: 14 }}>
          <label className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>Where</span>
            <select className="field" value={pipe} onChange={(e) => { setPipe(e.target.value); if (!e.target.value && outage === 'pipe') setOutage('none'); }}>
              <option value="">A random pipe</option>{pipes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select></label>
          <label className="stack" style={{ gap: 4 }}><span className="row spread sub" style={{ fontSize: 12 }}><span>Leak size</span><b className="num" style={{ color: 'var(--text)' }}>{size} m3/h</b></span>
            <input type="range" min={10} max={300} step={5} value={size} onChange={(e) => setSize(Number(e.target.value))} aria-label="Leak size in cubic metres per hour" /></label>
          <label className="stack" style={{ gap: 4 }}><span className="sub" style={{ fontSize: 12 }}>Sensors turned off</span>
            <select className="field" value={outage} onChange={(e) => setOutage(e.target.value as typeof outage)}>
              <option value="none">None, all 89 report</option>
              <option value="pipe" disabled={!pipe}>All sensors on the chosen pipe</option>
              <option value="flows">Every pipe flow meter</option>
            </select></label>
          <button className="btn primary" onClick={run} disabled={busy}><Play size={16} />{busy ? 'Running...' : 'Inject the leak'}</button>
        </div>
        <div style={{ minWidth: 0 }}>
          {!res ? <Empty title="Nothing injected yet">Pick a size and press the button. Try a small leak with the pipe&rsquo;s own sensors off.</Empty> : (
            <div className="stack" style={{ gap: 16 }}>
              <div className="row wrap" style={{ gap: 10 }}>
                <span className={`chip ${hit ? 'ok' : res.hitRank ? 'warn' : 'crit'}`}>{hit ? <CircleCheck size={13} /> : <CircleAlert size={13} />}{hit ? 'Located correctly' : res.hitRank ? `Real pipe was number ${res.hitRank} on the list` : 'Real pipe not on the list'}</span>
                <span className="muted">Hidden leak: <b style={{ color: 'var(--text)' }}>{res.truthName}</b>, {fmt(res.leakM3H)} m3/h{res.disabledSensors.length ? `, ${res.disabledSensors.length} sensors off` : ''}</span>
              </div>
              <div>
                <div className="card-title" style={{ marginBottom: 8 }}>Where the system thinks it is</div>
                <div className="stack" style={{ gap: 10 }}>
                  {res.localization.candidates.map((c) => (
                    <div key={c.assetId}>
                      <div className="row spread" style={{ marginBottom: 3 }}><span style={{ fontWeight: c.rank === 1 ? 650 : 400 }}>{c.rank}. {c.name}{c.assetId === res.truthAssetId && <span className="chip ok" style={{ marginInlineStart: 8 }}>real</span>}</span>
                        <span className="num"><b>{fmt(c.score * 100)}%</b> <span className="muted">match · {fmt(c.leakM3H)} m3/h</span></span></div>
                      <Meter value={c.score} color={c.assetId === res.truthAssetId ? 'var(--ok)' : 'var(--accent)'} label={`${c.name} match`} />
                    </div>))}
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>{res.localization.sensorsUsed} of {res.localization.sensorsTotal} sensors used{res.localization.ambiguous ? '. The top two are too close to call.' : '.'}</div>
              </div>
              <div>
                <div className="card-title" style={{ marginBottom: 8 }}>When each detector raised the alarm</div>
                <div className="row wrap" style={{ gap: 8 }}>
                  {res.detectors.map((d) => <span key={d.detectorId} className={`chip ${d.detected ? '' : 'crit'}`} style={{ height: 28 }}><Swatch c={COLOR[d.detectorId]} />{d.name}: <b className="num">{d.detected ? `${fmt(d.delayMin ?? 0)} min` : 'never'}</b>{d.detected && <span className="muted num">· {fmt(((d.delayMin ?? 0) / 60) * res.hourlyCost)} EGP</span>}</span>)}
                </div>
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Each minute of not knowing costs about {fmt(res.hourlyCost / 60)} EGP for a leak this size (cost model, current prices).</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function Localization({ l }: { l: LabLocalization }) {
  return (
    <Card title={<span className="row" style={{ gap: 8 }}><Radar size={16} />Finding the leaking pipe</span>} sub={`${fmt(l.runs)} hidden leaks, 9 candidate pipes, ${l.sensorsTotal} sensors`}>
      <div className="row" style={{ gap: 28, marginBottom: 14 }}>
        <div><div className="sub" style={{ fontSize: 12 }}>Right pipe first</div><div className="num" style={{ fontSize: 26, fontWeight: 650 }}>{fmt(l.top1 * 100)}%</div></div>
        <div><div className="sub" style={{ fontSize: 12 }}>In the top three</div><div className="num" style={{ fontSize: 26, fontWeight: 650 }}>{fmt(l.top3 * 100)}%</div></div>
      </div>
      <div className="stack" style={{ gap: 8 }}>
        {l.bySize.map((r) => <div key={r.leakM3H}><div className="row spread" style={{ fontSize: 13, marginBottom: 2 }}><span>{fmt(r.leakM3H)} m3/h</span><span className="num">{fmt(r.top1 * 100)}%</span></div><Meter value={r.top1} color="var(--accent)" label={`Located first at ${r.leakM3H} m3/h`} /></div>)}
      </div>
      <p className="muted" style={{ fontSize: 12, margin: '12px 0 0' }}>Each pipe has a stored fingerprint: how every sensor reacts to a leak there. The locator fits the live readings to one fingerprint plus a demand-error term. On this small synthetic network it is easy; with sensors off the table below shows where it gets harder.</p>
    </Card>
  );
}

function Sensors({ s }: { s: SensorValue }) {
  const rows = s.rows.slice(0, 9);
  const max = Math.max(...rows.map((r) => r.extraCost), 1);
  return (
    <Card title="Which sensors are worth the most?" sub={`Take one asset's sensors offline and look for a ${fmt(s.leakM3H)} m3/h burst. ${s.runsPerAsset} bursts per asset, found in ${fmt(s.baselineDelayMin ?? 0)} min with every sensor on.`} pad={false}>
      <div style={{ overflowX: 'auto' }}>
        <table className="table" style={{ minWidth: 560 }}>
          <thead><tr><th>Asset whose sensors go offline</th><th className="r">Bursts still found</th><th className="r">Extra delay</th><th style={{ width: '32%' }}>Extra cost per burst</th><th className="r">Located first</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.assetId}><td>{r.name}<span className="muted"> · {r.sensors.length} sensors</span></td><td className="r num">{fmt(r.detectionRate * 100)}%</td><td className="r num">{r.extraDelayMin > 0.5 ? `+${fmt(r.extraDelayMin)} min` : 'none'}</td>
                <td><div className="row" style={{ gap: 8 }}><div style={{ flex: 1 }}><Meter value={r.extraCost / max} color="var(--serious)" label={`${r.name} extra cost`} /></div><b className="num" style={{ minWidth: 64, textAlign: 'end' }}>{fmt(r.extraCost)}</b></div></td>
                <td className="r num">{fmt(r.localizationTop1 * 100)}%</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: 0, padding: '10px 16px 14px' }}>EGP at the current prices (cost model, {fmt(s.hourlyCost)} EGP an hour for this burst). The pipes at the top have the fewest neighbours that could witness a leak there, so a second sensor path there is the best place to spend money. Pump and reservoir sensors barely matter for bursts.</p>
    </Card>
  );
}

function Quality({ q }: { q: LabQuality }) {
  const live = useFetch<QualityNow>('/quality', 4000);
  const set = useUi((s) => s.set);
  const flagged = live.data?.flagged ?? [];
  return (
    <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))' }}>
      <Card title="Can the system tell a bad sensor from a real fault?" sub={`Flags are judged on a ${q.windowTicks}-minute window. A shared move across a sensor's asset or its kind is a process change, not a bad sensor.`}>
        <div className="stack" style={{ gap: 12 }}>
          {q.kinds.map((k) => (
            <div key={k.kind}><div className="row spread" style={{ marginBottom: 3 }}><span>{k.label}</span><span className="num"><b>{fmt(k.detectionRate * 100)}%</b> <span className="muted">found · typical {min(k.medianDelayMin)}</span></span></div><Meter value={k.detectionRate} color="var(--ok)" label={k.label} /></div>
          ))}
        </div>
        <div className="row wrap" style={{ gap: 8, marginTop: 14 }}>
          <span className="chip ok">{fmt(q.falseFlagsPer100SensorHours, 2)} false flags per 100 sensor-hours</span>
          <span className={`chip ${q.processFalseFlags ? 'warn' : 'ok'}`} style={{ whiteSpace: 'normal', height: 'auto', minHeight: 22, padding: '3px 8px' }}>{q.processFalseFlags} sensors wrongly flagged in {q.processRuns} real faults and weather events</span>
        </div>
      </Card>
      <Card title="Flags right now" sub={`${live.data?.sensorsTotal ?? 89} sensors checked every few seconds`}
        right={<button className="btn sm" onClick={() => set({ scenarioOpen: true })}><FlaskConical size={14} />Break a sensor</button>}>
        {!live.data ? <Skeleton h={80} /> : flagged.length === 0 ? <Empty title="Every sensor looks healthy">Open the scenario lab and start Sensor drift or Sensor dropout to see a flag appear.</Empty> : (
          <div className="stack" style={{ gap: 10 }}>{flagged.map((f) => <div key={f.sensorId} className="row" style={{ gap: 10, alignItems: 'flex-start' }}><span className={`chip ${f.status === 'drift' ? 'warn' : 'crit'}`}>{f.status}</span><div><b className="num">{sensorName(f.sensorId)}</b><div className="muted" style={{ fontSize: 12 }}>{f.detail}</div></div></div>)}</div>
        )}
      </Card>
    </section>
  );
}

export function Lab() {
  const bench = useFetch<LabBenchmark>('/lab/benchmark');
  const loc = useFetch<LabLocalization>('/lab/localization');
  const sensors = useFetch<SensorValue>('/lab/sensors', 30000);
  const quality = useFetch<LabQuality>('/lab/quality');
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>AI lab</h1><p>Four detectors, a leak locator and data-quality checks, each tested on faults injected into the twin. Nothing on this page is a stored number: it is measured by running the simulation.</p></div>
      </div>
      {bench.data ? <Benchmark b={bench.data} /> : bench.error ? <Card><Empty title="Could not run the benchmark">{bench.error}</Empty></Card>
        : <Card title="Running the benchmark" sub="The first time takes about 40 seconds: it simulates hundreds of faults and trains the models. Later visits are instant."><Skeleton h={220} /></Card>}
      <Trial />
      <section className="grid" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.6fr)' }}>
        {loc.data ? <Localization l={loc.data} /> : <Card><Skeleton h={220} /></Card>}
        {sensors.data ? <Sensors s={sensors.data} /> : <Card><Skeleton h={220} /></Card>}
      </section>
      {quality.data ? <Quality q={quality.data} /> : <Card title="Data quality"><Skeleton h={120} /></Card>}
      {bench.data && (
        <Card>
          <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
            <CircleAlert size={18} color="var(--text-3)" style={{ marginTop: 2, flexShrink: 0 }} />
            <div className="stack" style={{ gap: 6 }}><b>How to read these results</b>{bench.data.notes.map((n, i) => <div key={i} className="sub">{n}</div>)}</div>
          </div>
        </Card>
      )}
    </div>
  );
}
