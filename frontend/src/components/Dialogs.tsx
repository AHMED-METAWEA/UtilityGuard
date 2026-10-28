import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from 'react';
import { Command, FlaskConical, Search, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import type { Asset, Network } from '../api/types';
import { Badge, Button, DataState, EmptyState, NumberValue, Panel, Skeleton, StatusDot } from '../design/primitives';
import { networks, networkIcons } from './Sidebar';
import { loadResources } from '../api/client';

const TelemetryChart = lazy(() => import('./TelemetryChart'));

function Modal({ title, onClose, children, className = '' }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const root = ref.current;
    root?.querySelector<HTMLElement>('input, button')?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      if (event.key !== 'Tab') return;
      const focusables = root?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [href], [tabindex="0"]');
      if (!focusables?.length) return;
      const first = focusables[0]; const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, [onClose]);
  return <div className={`modal-scrim ${className}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div ref={ref} className="modal panel" role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><Button variant="icon" aria-label={t('common.close')} onClick={onClose}><X size={17} /></Button></div>{children}</div></div>;
}

export function ScenarioDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const scenarios = useStore((s) => s.scenarios);
  return <Modal title={t('scenario.title')} onClose={onClose}><div className="scenario-intro"><FlaskConical size={32} /><Badge>{t('app.phase')}</Badge><h3>{t('scenario.subtitle')}</h3><p>{t('scenario.placeholder')}</p></div><DataState status={scenarios.status} empty={!scenarios.data.length} title={t('scenario.empty')} detail={t('scenario.ready')} onRetry={() => void loadResources()}><div className="scenario-list">{scenarios.data.map((scenario) => <article key={scenario.id}><Badge>{t(`networks.${scenario.network}`)}</Badge><h3>{scenario.name}</h3><p>{scenario.description}</p></article>)}</div></DataState></Modal>;
}

export function CommandDialog({ onClose, onScenario, onLayer, onCharts }: { onClose: () => void; onScenario: () => void; onLayer: (network: Network) => void; onCharts: () => void }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const commands = [
    ...networks.map((network, i) => ({ label: t(`networks.${network}`), key: String(i + 1), icon: networkIcons[network], run: () => onLayer(network) })),
    { label: t('scenario.title'), key: 'L', icon: FlaskConical, run: onScenario },
    { label: t('commands.toggleCharts'), key: 'C', icon: Command, run: onCharts },
  ].filter((item) => item.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <Modal title={t('commands.title')} onClose={onClose}><div className="command-search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('commands.search')} aria-label={t('commands.search')} /></div><div className="command-list">{commands.map((command) => <button key={command.label} onClick={() => { onClose(); command.run(); }}><command.icon size={16} /><span>{command.label}</span><kbd>{command.key}</kbd></button>)}{!commands.length && <EmptyState title={t('commands.empty')} />}</div></Modal>;
}

export function AssetDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const { t } = useTranslation();
  const status = useStore((s) => s.assets[asset.id]);
  const alerts = useStore((s) => s.alerts);
  const sensor = useStore((s) => s.topology.data?.sensors.find((item) => item.assetId === asset.id));
  const readings = useStore((s) => sensor ? s.readings[sensor.id] : undefined);
  const forecast = useStore((s) => s.forecast[`${asset.network}:${asset.zoneId}`]);
  const ready = useStore((s) => s.snapshotReady);
  const connection = useStore((s) => s.connection);
  const loadState = ready ? 'ready' : connection === 'reconnecting' ? 'error' : 'loading';
  const assetStatus = status?.status ?? asset.status;
  return <Modal title={t('asset.title')} onClose={onClose} className="drawer-scrim"><div className="asset-detail"><Badge>{t(`networks.${asset.network}`)}</Badge><h3>{asset.name}</h3><p className="font-mono">{asset.id}</p><StatusDot status={assetStatus === 'normal' ? 'ok' : assetStatus === 'warning' ? 'warn' : assetStatus} label={t(`key.${assetStatus}`)} /><Panel><span>{t('asset.health')}</span><DataState status={loadState} empty={!status} title={t('asset.awaiting')}>{status && <NumberValue value={status.healthScore} unit={t('common.percent')} />}</DataState></Panel><h3 className="drawer-subtitle">{t('charts.title')}</h3><DataState status={loadState} empty={!sensor || !readings?.length} title={t('charts.empty')}><Suspense fallback={<Skeleton />}>{sensor && readings && <TelemetryChart sensor={sensor} readings={readings} />}</Suspense></DataState><h3 className="drawer-subtitle">{t('charts.forecast')}</h3>{forecast?.points.length ? <NumberValue value={forecast.points[0].predicted} unit={forecast.unit} digits={2} /> : <EmptyState title={t('asset.forecastEmpty')} />}<h3 className="drawer-subtitle">{t('asset.recentAlerts')}</h3><DataState status={alerts.status} empty={!alerts.data.some((a) => a.assetIds.includes(asset.id))} title={t('alerts.empty')}>{alerts.data.filter((a) => a.assetIds.includes(asset.id)).map((alert) => <p key={alert.id}>{alert.title}</p>)}</DataState></div></Modal>;
}
