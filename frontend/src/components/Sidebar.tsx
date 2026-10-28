import { ChevronRight, Droplets, FlaskConical, Layers, MapPin, Snowflake, Workflow, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Network } from '../api/types';
import { useStore } from '../store';
import { Button, DataState, Panel, PanelTitle, StatusDot, Toggle, Tooltip } from '../design/primitives';
import { loadResources } from '../api/client';

export const networkIcons = { water: Droplets, electricity: Zap, cooling: Snowflake };
export const networks: Network[] = ['water', 'electricity', 'cooling'];
const reload = () => { void loadResources(); };

export function Sidebar({ layers, toggleLayer, onScenario }: { layers: Record<Network, boolean>; toggleLayer: (network: Network) => void; onScenario: () => void }) {
  const { t } = useTranslation();
  const topology = useStore((s) => s.topology);
  const selectedZone = useStore((s) => s.selectedZone);
  const selectZone = useStore((s) => s.selectZone);
  return <aside className="left-rail" aria-label={t('layers.title')}>
    <Panel className="layer-panel"><PanelTitle title={t('layers.title')} icon={<Layers size={14} />} /><p className="panel-subtitle">{t('layers.subtitle')}</p><div className="layer-list">{networks.map((network, i) => {
      const Icon = networkIcons[network];
      return <div key={network} className={`layer-row network-${network} ${layers[network] ? 'is-active' : ''}`}><span className="network-icon"><Icon size={18} strokeWidth={1.6} /></span><span className="layer-copy"><strong>{t(`networks.${network}`)}</strong><small>{t(`layers.${network}`)}</small></span><Tooltip content={t('layers.shortcut', { network: t(`networks.${network}`), key: i + 1 })}><Toggle checked={layers[network]} onChange={() => toggleLayer(network)} label={t('layers.shortcut', { network: t(`networks.${network}`), key: i + 1 })} /></Tooltip></div>;
    })}</div><div className="rail-section"><PanelTitle title={t('zones.title')} icon={<MapPin size={13} />} /><DataState status={topology.status} empty={!topology.data?.zones.length} title={t('zones.empty')} detail={t('zones.detail')} onRetry={reload}><div className="zone-list"><button className={`zone-row ${selectedZone === null ? 'selected' : ''}`} onClick={() => selectZone(null)} aria-pressed={selectedZone === null}>{t('zones.all')}<ChevronRight size={13} /></button>{topology.data?.zones.map((zone) => <button key={zone.id} className={`zone-row ${selectedZone === zone.id ? 'selected' : ''}`} onClick={() => selectZone(zone.id)} aria-label={t('zones.select', { name: zone.name })} aria-pressed={selectedZone === zone.id}>{zone.name}<ChevronRight size={13} /></button>)}</div></DataState></div><div className="rail-section map-key"><PanelTitle title={t('key.title')} /><div className="key-grid"><StatusDot status="ok" label={t('key.normal')} /><StatusDot status="warn" label={t('key.warning')} /><StatusDot status="fault" label={t('key.fault')} /><StatusDot status="offline" label={t('key.offline')} /></div><div className="dependency"><Workflow size={13} />{t('key.dependency')}</div></div></Panel>
    <Panel className="scenario-card"><div className="scenario-icon"><FlaskConical size={19} /><span className="eyebrow">{t('scenario.tag')}</span></div><h2>{t('scenario.subtitle')}</h2><p>{t('scenario.description')}</p><Button onClick={onScenario}>{t('scenario.title')}<ChevronRight size={14} /></Button></Panel>
    <div className="rail-foot"><span className="eyebrow">{t('app.phase')}</span><span>{t('map.synthetic')}</span></div>
  </aside>;
}
