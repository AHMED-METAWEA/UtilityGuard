import { Component, useEffect, type ErrorInfo, type ReactNode } from 'react';
import { StreamClient } from './api/ws';
import { loadResources } from './api/client';
import { useStore } from './store';
import { useRoute } from './lib/route';
import { TopBar } from './shell/TopBar';
import { Live } from './pages/Live';

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

  return (
    <div className="app">
      <TopBar />
      <main id="main">
        {route.page === 'live' && <Live />}
        {route.page !== 'live' && <div className="page"><div className="card card-body">{route.page} (coming next)</div></div>}
      </main>
    </div>
  );
}
