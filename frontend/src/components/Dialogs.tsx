import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { Command, FlaskConical, Search, SlidersHorizontal, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import type { Asset, Assumption, AssumptionsUpdate, CalculationRecord, Network, Parameters, Scenario, ScenarioRunResponse } from '../api/types';
import { Badge, Button, DataState, EmptyState, NumberValue, Panel, Skeleton, StatusDot } from '../design/primitives';
import { networks, networkIcons } from './Sidebar';
import { CalculationView, CostTiles, SensitivityLines } from './FindingCard';
import { loadResources, request } from '../api/client';

const TelemetryChart = lazy(() => import('./TelemetryChart'));

export function Modal({ title, onClose, children, className = '', alert = false }: { alert?: boolean; title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const root = ref.current;
    root?.querySelector<HTMLElement>('input, select, button')?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      if (event.key !== 'Tab') return;
      const focusables = root?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [href], [tabindex="0"]');
      if (!focusables?.length) return;
      const first = focusables[0]; const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, [onClose]);
  return <div className={`modal-scrim ${className}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div ref={ref} className="modal panel" role={alert ? 'alertdialog' : 'dialog'} aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><Button variant="icon" aria-label={t('common.close')} onClick={onClose}><X size={17} /></Button></div>{children}</div></div>;
}

function ScenarioCard({ scenario, selected }: { scenario: Scenario; selected: boolean }) {
  const { t } = useTranslation();
  const topology = useStore((s) => s.topology.data);
  const [parameters, setParameters] = useState<Parameters>(() => Object.fromEntries(scenario.parameters.map((p) => [p.name, p.default])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ref = useRef<HTMLElement>(null);
  useEffect(() => { if (selected) ref.current?.scrollIntoView({ block: 'center' }); }, [selected]);
  const Icon = scenario.network === 'multi' ? FlaskConical : networkIcons[scenario.network];
  async function run() {
    setBusy(true); setError('');
    try { await request<ScenarioRunResponse>('/scenarios/run', { method: 'POST', body: JSON.stringify({ scenarioId: scenario.id, parameters }) }); }
    catch (err) { setError(err instanceof Error ? err.message : t('common.unavailable')); }
    finally { setBusy(false); }
  }
  return <article ref={ref} className={selected ? 'selected-scenario' : ''}><div className="scenario-title"><Icon size={18} /><h3>{scenario.name}</h3><Badge>{t(`networks.${scenario.network}`)}</Badge></div><p>{scenario.description}</p><form onSubmit={(event) => { event.preventDefault(); void run(); }}>{scenario.parameters.map((parameter) => {
    const value = parameters[parameter.name];
    const prefix = String(parameter.default).replace(/-\d.*$/, '');
    const inferred = parameter.type === 'asset' ? topology?.assets.filter((a) => a.id.startsWith(prefix + '-')).map((a) => ({ value: a.id, label: a.name })) : topology?.sensors.filter((sensor) => sensor.kind === topology.sensors.find((s) => s.id === parameter.default)?.kind).map((sensor) => ({ value: sensor.id, label: sensor.id }));
    const options = parameter.options?.map((o) => typeof o === 'string' ? { value: o, label: o } : o) ?? inferred ?? [];
    if (!options.some((o) => o.value === String(parameter.default))) options.unshift({ value: String(parameter.default), label: String(parameter.default) });
    return <label className="scenario-parameter" key={parameter.name}><span>{t(`parameters.${parameter.name}`, { defaultValue: parameter.name })}{parameter.type === 'number' && <output>{String(value)}</output>}</span>{parameter.type === 'number' ? <input type="range" min={parameter.min} max={parameter.max} step={Number.isInteger(parameter.default) ? 1 : 0.01} value={Number(value)} onChange={(event) => setParameters({ ...parameters, [parameter.name]: Number(event.target.value) })} /> : <select value={String(value)} onChange={(event) => setParameters({ ...parameters, [parameter.name]: event.target.value })}>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>}</label>;
  })}<Button type="submit" variant="primary" disabled={busy}>{t(busy ? 'lab.starting' : 'lab.run')}</Button></form>{error && <p className="inline-error" role="alert">{error}</p>}</article>;
}

export function ScenarioDialog({ onClose, initialScenario }: { onClose: () => void; initialScenario?: string }) {
  const { t } = useTranslation();
  const scenarios = useStore((s) => s.scenarios);
  const runs = useStore((s) => s.scenarioRuns);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  async function control(path: string, body: object, key: string) {
    setBusy(key); setError('');
    try { await request(path, { method: 'POST', body: JSON.stringify(body) }); setConfirm(false); }
    catch (err) { setError(err instanceof Error ? err.message : t('common.unavailable')); }
    finally { setBusy(null); }
  }
  if (confirm) return <Modal title={t('lab.reset')} alert onClose={() => setConfirm(false)}><p className="dialog-description">{t('lab.confirmReset')}</p><div className="decision-actions"><Button variant="primary" disabled={busy !== null} onClick={() => void control('/scenarios/reset', {}, 'reset')}>{t(busy === 'reset' ? 'lab.resetting' : 'lab.confirm')}</Button><Button disabled={busy !== null} onClick={() => setConfirm(false)}>{t('lab.cancel')}</Button></div>{error && <p className="inline-error" role="alert">{error}</p>}</Modal>;
  return <Modal title={t('scenario.title')} onClose={onClose}><p className="dialog-description">{t('lab.description')}</p><div className="running-scenarios" aria-live="polite">{Object.values(runs).filter((run) => run.state === 'running').map((run) => <div key={run.runId}><StatusDot status="warn" label={scenarios.data.find((s) => s.id === run.scenarioId)?.name ?? run.scenarioId} /><small>{new Date(run.startedAt).toLocaleTimeString()}</small><Button disabled={busy !== null} onClick={() => void control('/scenarios/stop', { runId: run.runId }, run.runId)}>{t(busy === run.runId ? 'lab.stopping' : 'lab.stop')}</Button></div>)}</div><DataState status={scenarios.status} empty={!scenarios.data.length} title={t('scenario.empty')} onRetry={() => void loadResources()}><div className="scenario-list">{scenarios.data.map((scenario) => <ScenarioCard key={scenario.id} scenario={scenario} selected={initialScenario === scenario.id} />)}</div></DataState><div className="reset-twin"><Button onClick={() => setConfirm(true)}>{t('lab.reset')}</Button></div>{error && <p className="inline-error" role="alert">{error}</p>}</Modal>;
}

export function AssumptionsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [assumptions, setAssumptions] = useState<Assumption[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { const controller = new AbortController(); request<Assumption[]>('/assumptions', { signal: controller.signal }).then((data) => { setAssumptions(data); setValues(Object.fromEntries(data.map((a) => [a.key, String(a.value)]))); setStatus('ready'); }).catch(() => { if (!controller.signal.aborted) setStatus('error'); }); return () => controller.abort(); }, [attempt]);
  async function save() {
    setBusy(true); setError(''); setFeedback('');
    try {
      const changed = Object.fromEntries(assumptions.filter((a) => Number(values[a.key]) !== a.value).map((a) => [a.key, Number(values[a.key])]));
      const result = await request<AssumptionsUpdate>('/assumptions', { method: 'PUT', body: JSON.stringify(changed) });
      setAssumptions(result.assumptions); setValues(Object.fromEntries(result.assumptions.map((a) => [a.key, String(a.value)]))); setFeedback(t('assumptions.saved'));
    } catch (err) { setError(err instanceof Error ? err.message : t('common.unavailable')); }
    finally { setBusy(false); }
  }
  return <Modal title={t('decision.assumptions')} onClose={onClose} className="drawer-scrim"><p className="dialog-description">{t('assumptions.description')}</p><DataState status={status} empty={!assumptions.length} title={t('assumptions.empty')} onRetry={() => { setStatus('loading'); setAttempt((n) => n + 1); }}><form onSubmit={(event) => { event.preventDefault(); void save(); }} className="assumptions-form">{assumptions.map((a) => <label key={a.key}><span><strong>{a.name}</strong>{(a.userChanged || Number(values[a.key]) !== a.value) && <Badge tone="warn">{t('assumptions.changed')}</Badge>}<small>{a.source}</small></span><span className="assumption-value"><input type="number" required step="any" value={values[a.key] ?? ''} onChange={(event) => setValues({ ...values, [a.key]: event.target.value })} /><small>{a.unit}</small></span></label>)}<Button type="submit" variant="primary" disabled={busy || assumptions.every((a) => Number(values[a.key]) === a.value)}>{t(busy ? 'assumptions.saving' : 'assumptions.save')}</Button></form></DataState>{feedback && <p role="status" className="inline-success">{feedback}</p>}{error && <p role="alert" className="inline-error">{error}</p>}</Modal>;
}

export function CalculationDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useTranslation();
  const [record, setRecord] = useState<CalculationRecord | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { const controller = new AbortController(); request<CalculationRecord>(`/calculations/${encodeURIComponent(id)}`, { signal: controller.signal }).then((data) => { setRecord(data); setStatus('ready'); }).catch(() => { if (!controller.signal.aborted) setStatus('error'); }); return () => controller.abort(); }, [id, attempt]);
  return <Modal title={t('decision.calculations')} onClose={onClose} className="drawer-scrim"><DataState status={status} empty={!record} title={t('decision.noCalculation')} onRetry={() => { setStatus('loading'); setAttempt((n) => n + 1); }}>{record && <><p className="dialog-description">{record.id} · {new Date(record.createdAt).toLocaleString()}</p><CostTiles cost={record.cost} />{record.explanation.calculations.map((c) => <CalculationView key={c.name} calculation={c} />)}<SensitivityLines explain={record.explanation} /><h3 className="drawer-subtitle">{t('decision.breakdown')}</h3>{record.cost.breakdown.map((item, i) => <div className="breakdown-row" key={i}><span>{item.label}<small>{item.cause}</small></span><NumberValue value={item.amount} unit={record.cost.currency} digits={2} /></div>)}</>}</DataState></Modal>;
}

export function CommandDialog({ onClose, onScenario, onLayer, onCharts, onAssumptions }: { onAssumptions: () => void; onClose: () => void; onScenario: () => void; onLayer: (network: Network) => void; onCharts: () => void }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const commands = [
    ...networks.map((network, i) => ({ label: t(`networks.${network}`), key: String(i + 1), icon: networkIcons[network], run: () => onLayer(network) })),
    { label: t('scenario.title'), key: 'S', icon: FlaskConical, run: onScenario },
    { label: t('decision.assumptions'), key: '', icon: SlidersHorizontal, run: onAssumptions },
    { label: t('commands.toggleCharts'), key: 'C', icon: Command, run: onCharts },
  ].filter((item) => item.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <Modal title={t('commands.title')} onClose={onClose}><div className="command-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('commands.search')} aria-label={t('commands.search')} /></div><div className="command-list">{commands.map((command) => <button key={command.label} onClick={() => { onClose(); command.run(); }}><command.icon size={16} /><span>{command.label}</span><kbd>{command.key}</kbd></button>)}{!commands.length && <EmptyState title={t('commands.empty')} />}</div></Modal>;
}

export function AssetDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const { t } = useTranslation();
  const status = useStore((s) => s.assets[asset.id]);
  const alerts = useStore((s) => s.alerts);
  const topology = useStore((s) => s.topology.data);
  const readings = useStore((s) => s.readings);
  const sensors = topology?.sensors.filter((s) => s.assetId === asset.id) ?? [];
  const ready = useStore((s) => s.snapshotReady);
  const connection = useStore((s) => s.connection);
  const loadState = ready ? 'ready' : connection === 'reconnecting' ? 'error' : 'loading';
  const assetStatus = status?.status ?? asset.status;
  return <Modal title={t('asset.title')} onClose={onClose} className="drawer-scrim"><div className="asset-detail"><Badge>{t(`networks.${asset.network}`)}</Badge><h3>{asset.name}</h3><p>{topology?.zones.find((z) => z.id === asset.zoneId)?.name ?? asset.zoneId}</p><p className="font-mono">{asset.id}</p><StatusDot status={assetStatus === 'normal' ? 'ok' : assetStatus === 'warning' ? 'warn' : assetStatus} label={t(`key.${assetStatus}`)} /><Panel><span>{t('asset.health')}</span><DataState status={loadState} empty={!status} title={t('asset.awaiting')}>{status && <div className="health-gauge"><meter min="0" max="100" value={status.healthScore} aria-label={t('asset.health')} /><NumberValue value={status.healthScore} unit="/100" /></div>}</DataState></Panel><Panel><span>{t('asset.failure')}</span>{status?.failureProbability !== undefined ? <NumberValue value={status.failureProbability * 100} unit="%" digits={1} /> : <span>{t('common.none')}</span>}</Panel><h3 className="drawer-subtitle">{t('charts.title')}</h3><p>{t('asset.chartLegend')}</p><DataState status={loadState} empty={!sensors.length || !sensors.some((sensor) => readings[sensor.id]?.length)} title={t('charts.empty')}><Suspense fallback={<Skeleton />}>{sensors.map((sensor) => <TelemetryChart key={sensor.id} sensor={sensor} readings={readings[sensor.id] ?? []} />)}</Suspense></DataState><h3 className="drawer-subtitle">{t('asset.recentAlerts')}</h3><DataState status={alerts.status} empty={!alerts.data.some((a) => a.assetIds.includes(asset.id))} title={t('alerts.empty')}>{alerts.data.filter((a) => a.assetIds.includes(asset.id)).sort((a, b) => b.detectedAt.localeCompare(a.detectedAt)).map((alert) => <article className={`incident incident-${alert.severity}`} key={alert.id}><Badge tone={alert.severity === 'critical' ? 'fault' : 'warn'}>{t(`alerts.${alert.severity}`)}</Badge><h4>{alert.title}</h4><p>{alert.summary}</p><small>{t(`alerts.${alert.state === 'open' ? 'openState' : alert.state}`)} · {new Date(alert.detectedAt).toLocaleTimeString()}</small></article>)}</DataState></div></Modal>;
}
