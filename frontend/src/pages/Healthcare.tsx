import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { Card } from '../ui';
import { embedUrl, healthUrl } from '../lib/health';
import { go } from '../lib/route';

type State = 'checking' | 'up' | 'down';

/** The healthcare team's dashboard exactly as they built it, shown inside the twin. Nothing of theirs is copied or changed: it is their own Streamlit app on its own
 * port. The numbers of both projects meet in the joint table on the Executive page, not here. */
export function Healthcare() {
  const base = healthUrl(window.location, import.meta.env.VITE_HEALTH_URL as string | undefined);
  const [state, setState] = useState<State>('checking');
  useEffect(() => {
    let stop = false, timer: number | undefined;
    const probe = () => {
      // no-cors: the answer is opaque, but it only resolves when something is listening on that port
      fetch(`${base}/_stcore/health`, { mode: 'no-cors', cache: 'no-store' }).then(() => { if (!stop) setState('up'); }).catch(() => {
        if (stop) return;
        setState('down');
        timer = window.setTimeout(probe, 4000);
      });
    };
    probe();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [base]);

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Healthcare investment</h1><p>The healthcare team's own dashboard, running as they built it. Its numbers are theirs; the twin did not change them. Both sides meet in the joint table on the <button type="button" className="btn sm" onClick={() => go('executive')}>Executive page</button>.</p></div>
        <a className="btn sm" href={base} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden />Open on its own</a>
      </div>
      {state === 'up'
        ? <iframe title="Healthcare investment dashboard" src={embedUrl(base)} style={{ width: '100%', height: 'calc(100vh - 210px)', minHeight: 560, border: '1px solid var(--border)', borderRadius: 12, background: 'var(--surface)' }} />
        : <Card title={state === 'checking' ? 'Looking for the dashboard' : 'The healthcare dashboard is not running'} sub={base}>
          <div className="stack" style={{ gap: 8 }}>
            <span>{state === 'checking' ? 'Checking whether it answers.' : 'Start both projects together from the repository folder, then this page fills in by itself:'}</span>
            {state === 'down' && <code style={{ padding: '8px 10px', background: 'var(--surface-2)', borderRadius: 8 }}>./run_all.sh</code>}
          </div>
        </Card>}
    </div>
  );
}
