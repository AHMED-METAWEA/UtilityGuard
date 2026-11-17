import { useEffect, useMemo, useState } from 'react';
import { FlaskConical, Play, RotateCcw, Square } from 'lucide-react';
import { post } from '../api/client';
import type { Network, Parameters, Scenario, ScenarioParameter, ScenarioRunResponse } from '../api/types';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { Drawer, NetIcon, Skeleton, Empty } from '../ui';
import { clock } from '../lib/format';

const ICON: Record<string, Network> = { water: 'water', electricity: 'electricity', cooling: 'cooling' };

function ParamField({ p, value, onChange, names }: { p: ScenarioParameter; value: string | number | boolean; onChange: (v: string | number) => void; names: Record<string, string> }) {
  const label = p.name.replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase());
  if (p.type === 'number') {
    return (
      <label className="stack" style={{ gap: 4 }}>
        <span className="row spread"><span className="sub">{label}</span><b className="num">{String(value)}</b></span>
        <input type="range" min={p.min ?? 0} max={p.max ?? 1} step={((p.max ?? 1) - (p.min ?? 0)) / 50} value={Number(value)} onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    );
  }
  const opts = (p.options ?? []).map((o) => (typeof o === 'string' ? { label: names[o] ?? o, value: o } : o));
  return (
    <label className="stack" style={{ gap: 4 }}>
      <span className="sub">{label}</span>
      <select className="field" value={String(value)} onChange={(e) => onChange(e.target.value)}>{opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
    </label>
  );
}

export function ScenarioLab() {
  const open = useUi((s) => s.scenarioOpen);
  const initial = useUi((s) => s.initialScenario);
  const set = useUi((s) => s.set); const notify = useUi((s) => s.notify);
  const scenarios = useStore((s) => s.scenarios);
  const runs = useStore((s) => s.scenarioRuns);
  const topology = useStore((s) => s.topology.data);
  const [params, setParams] = useState<Record<string, Parameters>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const names = useMemo(() => Object.fromEntries([...(topology?.assets ?? []).map((a) => [a.id, a.name]), ...(topology?.sensors ?? []).map((s) => [s.id, s.id])]), [topology]);

  useEffect(() => { if (open && initial) document.getElementById(`scn-${initial}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }); }, [open, initial]);
  if (!open) return null;
  const close = () => set({ scenarioOpen: false, initialScenario: null });
  const running = Object.values(runs).filter((r) => r.state === 'running');
  const valueOf = (sc: Scenario, p: ScenarioParameter) => params[sc.id]?.[p.name] ?? p.default;

  const run = async (sc: Scenario) => {
    setBusy(sc.id);
    try {
      const parameters = Object.fromEntries(sc.parameters.map((p) => [p.name, valueOf(sc, p)]));
      await post<ScenarioRunResponse>('/scenarios/run', { scenarioId: sc.id, parameters });
      notify(`${sc.name} started. Watch the map and the incident panel.`); close();
    } catch (e) { notify(`Could not start: ${(e as Error).message}`); } finally { setBusy(null); }
  };
  const stop = async (runId: string) => { try { await post('/scenarios/stop', { runId }); notify('Scenario stopped. The twin is recovering.'); } catch (e) { notify((e as Error).message); } };
  const reset = async () => { try { await post('/scenarios/reset'); setConfirmReset(false); notify('Twin reset to its normal baseline.'); close(); } catch (e) { notify((e as Error).message); } };

  return (
    <Drawer title={<span className="row" style={{ gap: 8 }}><FlaskConical size={18} />Scenario lab</span>} onClose={close}
      foot={confirmReset
        ? <><span className="sub" style={{ marginInlineEnd: 'auto', alignSelf: 'center' }}>Stop every scenario and clear all incidents?</span><button className="btn" onClick={() => setConfirmReset(false)}>Cancel</button><button className="btn danger" onClick={reset}>Reset twin</button></>
        : <button className="btn" onClick={() => setConfirmReset(true)}><RotateCcw size={15} />Reset twin</button>}>
      {running.length > 0 && (
        <div className="stack" style={{ gap: 8 }}>
          <div className="card-title">Running now</div>
          {running.map((r) => (
            <div key={r.runId} className="row spread card" style={{ padding: '10px 12px', background: 'var(--surface-2)' }}>
              <span><b>{scenarios.data.find((s) => s.id === r.scenarioId)?.name ?? r.scenarioId}</b><span className="muted" style={{ marginInlineStart: 8, fontSize: 12 }}>since {clock(r.startedAt)}</span></span>
              <button className="btn sm danger" onClick={() => stop(r.runId)}><Square size={13} />Stop</button>
            </div>
          ))}
        </div>
      )}
      <div className="card-title">Scenarios</div>
      {scenarios.status === 'loading' ? [0, 1, 2].map((i) => <Skeleton key={i} h={92} />)
        : scenarios.status === 'error' ? <Empty title="Could not load scenarios">Check that the backend is running.</Empty>
        : scenarios.data.map((sc) => (
          <section key={sc.id} id={`scn-${sc.id}`} className="card" style={{ padding: 14, outline: initial === sc.id ? '2px solid var(--accent)' : undefined }}>
            <div className="row spread" style={{ alignItems: 'flex-start' }}>
              <div style={{ minWidth: 0 }}>
                <div className="row" style={{ gap: 8, fontWeight: 600 }}>{sc.network !== 'multi' ? <NetIcon n={ICON[sc.network]} /> : <span className="row" style={{ gap: 2 }}><NetIcon n="water" size={13} /><NetIcon n="electricity" size={13} /><NetIcon n="cooling" size={13} /></span>}{sc.name}</div>
                <div className="sub" style={{ margin: '2px 0 10px' }}>{sc.description}</div>
              </div>
              <button className="btn primary sm" disabled={busy === sc.id} onClick={() => run(sc)}><Play size={13} />Run</button>
            </div>
            <div className="stack" style={{ gap: 10 }}>
              {sc.parameters.map((p) => <ParamField key={p.name} p={p} names={names} value={valueOf(sc, p)} onChange={(v) => setParams((o) => ({ ...o, [sc.id]: { ...o[sc.id], [p.name]: v } }))} />)}
            </div>
          </section>
        ))}
    </Drawer>
  );
}
