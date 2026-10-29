import { Component, useEffect, type ErrorInfo, type ReactNode } from 'react';
import { StreamClient } from './api/ws';
import { loadResources } from './api/client';
import { useStore } from './store';
import { useRoute } from './lib/route';
import { TopBar } from './shell/TopBar';
import { Live } from './pages/Live';
import { Incidents } from './pages/Incidents';
import { Savings } from './pages/Savings';
import { Forecast } from './pages/Forecast';
import { Lab } from './pages/Lab';
import { Audit } from './pages/Audit';
import { Executive } from './pages/Executive';
import { ScenarioLab } from './panels/ScenarioLab';
import { ChatDock } from './panels/ChatDock';
import { AssetDrawer, AssumptionsDrawer, CalcDrawer } from './panels/Drawers';
import { Palette, Toast } from './panels/Palette';
import { useUi } from './store/ui';
import { setTheme, resolve } from './lib/theme';

export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error(error, info.componentStack); }
  render() {
    if (this.state.failed) return <div className="page"><div className="card card-body"><h1 style={{ marginTop: 0 }}>Something went wrong while drawing this page</h1><p className="sub">The data feed is still running. Reload to try again.</p><button className="btn primary" onClick={() => window.location.reload()}>Reload</button></div></div>;
    return this.props.children;
  }
}

export default function App() {
  const route = useRoute();
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
    const on = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
      const ui = useUi.getState();
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); ui.set({ paletteOpen: !ui.paletteOpen }); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === 's') ui.set({ scenarioOpen: true });
      else if (k === 'c') ui.set({ chatOpen: !ui.chatOpen });
      else if (k === 't') setTheme(resolve() === 'dark' ? 'light' : 'dark');
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  return (
    <div className="app">
      <TopBar />
      <main id="main">
        {route.page === 'live' && <Live />}
        {route.page === 'incidents' && <Incidents id={route.id} />}
        {route.page === 'savings' && <Savings id={route.id} />}
        {route.page === 'forecast' && <Forecast />}
        {route.page === 'lab' && <Lab />}
        {route.page === 'executive' && <Executive />}
        {route.page === 'audit' && <Audit id={route.id} />}
      </main>
      <ChatDock />
      <ScenarioLab />
      <AssetDrawer />
      <AssumptionsDrawer />
      <CalcDrawer />
      <Palette />
      <Toast />
    </div>
  );
}
