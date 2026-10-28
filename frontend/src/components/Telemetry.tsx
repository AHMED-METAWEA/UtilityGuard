import { lazy, Suspense } from 'react';
import { Activity, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { DataState, Panel, Skeleton } from '../design/primitives';
import type { Network } from '../api/types';

const TelemetryChart = lazy(() => import('./TelemetryChart'));
export function Telemetry({ expanded, toggle, layers }: { expanded: boolean; toggle: () => void; layers: Record<Network, boolean> }) {
  const { t } = useTranslation();
  const readings = useStore((s) => s.readings);
  const topology = useStore((s) => s.topology);
  const ready = useStore((s) => s.snapshotReady);
  const connection = useStore((s) => s.connection);
  const zone = useStore((s) => s.selectedZone);
  const available = (topology.data?.sensors ?? []).filter((s) => layers[s.network] && readings[s.id]?.length && (!zone || topology.data?.assets.find((a) => a.id === s.assetId)?.zoneId === zone));
  const sensors = (['water', 'electricity', 'cooling'] as const).flatMap((network) => {
    const sensor = available.find((s) => s.network === network);
    return sensor ? [sensor] : [];
  });
  const state = ready || Object.keys(readings).length ? 'ready' : connection === 'reconnecting' || topology.status === 'error' ? 'error' : 'loading';
  return <Panel className={`telemetry-panel ${expanded ? 'expanded' : ''}`}><button className="telemetry-heading" onClick={toggle} aria-expanded={expanded} aria-controls="telemetry-body" aria-label={t(`charts.${expanded ? 'collapse' : 'expand'}`)}><span><Activity size={15} /><strong>{t('charts.title')}</strong><small>{t('charts.subtitle')}</small></span><span className="telemetry-meta"><span className="measured-dot" />{t('charts.measured')}{expanded ? <ChevronDown size={15} /> : <ChevronUp size={15} />}</span></button>{expanded && <div id="telemetry-body"><DataState status={state} empty={!sensors.length} title={t('charts.empty')} detail={t('charts.detail')}><div className="signal-grid"><Suspense fallback={<Skeleton />}>{sensors.map((sensor) => <TelemetryChart key={sensor.id} sensor={sensor} readings={readings[sensor.id]} />)}</Suspense></div></DataState></div>}</Panel>;
}
