import { Component, lazy, Suspense, useCallback, useEffect, useState, type ErrorInfo, type ReactNode } from 'react';
import { motion, MotionConfig } from 'framer-motion';
import { Command, Crosshair } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from './i18n';
import type { Asset, Network } from './api/types';
import { StreamClient } from './api/ws';
import { loadResources } from './api/client';
import { useStore } from './store';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { ActivityPanel } from './components/ActivityPanel';
import { ChatPanel } from './components/ChatPanel';
import { Telemetry } from './components/Telemetry';
import { AssetDialog, AssumptionsDialog, CalculationDialog, CommandDialog, ScenarioDialog } from './components/Dialogs';
import { Button, Skeleton } from './design/primitives';

const MapView = lazy(() => import('./components/MapView'));

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info.componentStack); }
  render() {
    if (this.state.failed) return <div className="fatal-error"><h1>{i18n.t('errors.render')}</h1><p>{i18n.t('errors.renderDetail')}</p><Button onClick={() => window.location.reload()}>{i18n.t('errors.reload')}</Button></div>;
    return this.props.children;
  }
}

export default function App() {
  const { t } = useTranslation();
  const [layers, setLayers] = useState<Record<Network, boolean>>({ water: true, electricity: true, cooling: true });
  const [charts, setCharts] = useState(true);
  const [dialog, setDialog] = useState<'scenario' | 'commands' | 'assumptions' | null>(null);
  const [initialScenario, setInitialScenario] = useState<string>();
  const [calculation, setCalculation] = useState<string | null>(null);
  const closeCalculation = useCallback(() => setCalculation(null), []);
  const openAssumptions = useCallback(() => setDialog('assumptions'), []);
  const [asset, setAsset] = useState<Asset | null>(null);
  const closeDialog = useCallback(() => setDialog(null), []);
  const closeAsset = useCallback(() => setAsset(null), []);
  const openScenario = useCallback(() => { setInitialScenario(undefined); setDialog('scenario'); }, []);
  const openQuickScenario = useCallback((id: string) => { setInitialScenario(id); setDialog('scenario'); }, []);
  const toggleCharts = useCallback(() => setCharts((value) => !value), []);
  const toggleLayer = useCallback((network: Network) => setLayers((old) => ({ ...old, [network]: !old[network] })), []);

  useEffect(() => {
    const abort = new AbortController();
    void loadResources(abort.signal);
    const client = new StreamClient(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/stream`);
    client.start();
    const unsub = useStore.subscribe((state) => {
      if (state.connection === 'reconnecting') {
        const patch: Partial<ReturnType<typeof useStore.getState>> = {};
        if (state.findings.status === 'loading') patch.findings = { data: [], status: 'error' };
        if (state.chat.status === 'loading') patch.chat = { data: [], status: 'error' };
        if (Object.keys(patch).length) useStore.setState(patch);
      }
    });
    return () => { abort.abort(); unsub(); client.stop(); };
  }, []);

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setDialog((current) => current === 'commands' ? null : 'commands'); return; }
      if (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || event.metaKey || event.ctrlKey || event.altKey || dialog || asset || calculation) return;
      const network = ({ '1': 'water', '2': 'electricity', '3': 'cooling' } as const)[event.key as '1' | '2' | '3'];
      if (network) toggleLayer(network);
      if (['s', 'l'].includes(event.key.toLowerCase())) openScenario();
      if (event.key.toLowerCase() === 'c') toggleCharts();
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [toggleLayer, openScenario, toggleCharts, dialog, asset, calculation]);

  return <MotionConfig reducedMotion="user"><div className="app-shell"><a href="#operations" className="skip-link">{t('app.skip')}</a><Header onScenario={openScenario} onCommands={() => setDialog('commands')} /><main id="operations" className="operations" tabIndex={-1}><Suspense fallback={<div className="map-loading"><Skeleton /></div>}><MapView layers={layers} onSelectAsset={setAsset} /></Suspense><motion.div className="workspace" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }}><Sidebar layers={layers} toggleLayer={toggleLayer} onScenario={openScenario} onQuickScenario={openQuickScenario} /><div className="center-workspace"><div className="map-context"><Crosshair size={14} /><span>{t('app.regionShort')}</span><span className="context-line" /><span>{t('layers.all')}</span></div><div className="center-bottom"><button className="command-hint" onClick={() => setDialog('commands')}><Command size={12} />{t('commands.hint')}<kbd>{t('commands.shortcut')}</kbd></button><Telemetry expanded={charts} toggle={toggleCharts} layers={layers} /></div></div><aside className="right-rail" aria-label={t('alerts.title')}><ActivityPanel onAssumptions={openAssumptions} onCalculation={setCalculation} /><ChatPanel onCalculation={setCalculation} /></aside></motion.div></main>{dialog === 'scenario' && <ScenarioDialog onClose={closeDialog} initialScenario={initialScenario} />}{dialog === 'commands' && <CommandDialog onClose={closeDialog} onScenario={openScenario} onLayer={toggleLayer} onCharts={toggleCharts} onAssumptions={openAssumptions} />}{dialog === 'assumptions' && <AssumptionsDialog onClose={closeDialog} />}{calculation && <CalculationDialog key={calculation} id={calculation} onClose={closeCalculation} />}{asset && <AssetDialog asset={asset} onClose={closeAsset} />}</div></MotionConfig>;
}
