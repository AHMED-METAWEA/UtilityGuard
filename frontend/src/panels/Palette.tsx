import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Command, CornerDownLeft, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { post } from '../api/client';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { go, PAGES, type Page } from '../lib/route';
import { setTheme, useTheme } from '../lib/theme';

const TITLE: Record<Page, string> = { live: 'Live twin', incidents: 'Incidents', savings: 'Savings', forecast: 'Forecast and models', executive: 'Executive' };
interface Cmd { id: string; label: string; hint: string; run: () => void }

export function Palette() {
  const open = useUi((s) => s.paletteOpen); const set = useUi((s) => s.set); const notify = useUi((s) => s.notify);
  const scenarios = useStore((s) => s.scenarios.data);
  const { theme } = useTheme();
  const { i18n } = useTranslation();
  const [q, setQ] = useState(''); const [i, setI] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setQ(''); setI(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  const cmds: Cmd[] = useMemo(() => {
    const close = () => set({ paletteOpen: false });
    return [
      ...PAGES.map((p, n) => ({ id: `go-${p}`, label: `Go to ${TITLE[p]}`, hint: `Page ${n + 1}`, run: () => { go(p); close(); } })),
      { id: 'lab', label: 'Open the scenario lab', hint: 'S', run: () => { set({ paletteOpen: false, scenarioOpen: true }); } },
      ...scenarios.map((s) => ({ id: `run-${s.id}`, label: `Run scenario: ${s.name}`, hint: 'with defaults', run: () => {
        const parameters = Object.fromEntries(s.parameters.map((p) => [p.name, p.default]));
        close(); post('/scenarios/run', { scenarioId: s.id, parameters }).then(() => notify(`${s.name} started.`)).catch((e: Error) => notify(e.message));
      } })),
      { id: 'chat', label: 'Ask the team', hint: 'C', run: () => set({ paletteOpen: false, chatOpen: true }) },
      { id: 'assume', label: 'Edit assumptions', hint: 'prices and parameters', run: () => set({ paletteOpen: false, assumptionsOpen: true }) },
      { id: 'theme', label: theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode', hint: 'T', run: () => { setTheme(theme === 'dark' ? 'light' : 'dark'); close(); } },
      { id: 'lang', label: i18n.language === 'ar' ? 'Switch to English' : 'التبديل إلى العربية', hint: 'Language', run: () => { void i18n.changeLanguage(i18n.language === 'ar' ? 'en' : 'ar'); close(); } },
      { id: 'reset', label: 'Reset the twin to its baseline', hint: 'ends all scenarios', run: () => { close(); post('/scenarios/reset').then(() => notify('Twin reset to its normal baseline.')).catch((e: Error) => notify(e.message)); } },
    ];
  }, [scenarios, theme, i18n, set, notify]);

  const list = cmds.filter((c) => `${c.label} ${c.hint}`.toLowerCase().includes(q.toLowerCase()));
  if (!open) return null;
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setI((x) => Math.min(x + 1, list.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setI((x) => Math.max(x - 1, 0)); }
    else if (e.key === 'Enter') { list[i]?.run(); }
    else if (e.key === 'Escape') set({ paletteOpen: false });
  };
  return createPortal(<>
    <div className="scrim" onClick={() => set({ paletteOpen: false })} />
    <div className="modal" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKey}>
      <div className="row" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', gap: 10 }}>
        <Search size={18} color="var(--text-3)" />
        <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setI(0); }} placeholder="Type a command or a page" aria-label="Command" style={{ flex: 1, border: 0, outline: 0, background: 'transparent', fontSize: 15 }} />
        <span className="chip"><Command size={12} />K</span>
      </div>
      <div role="listbox" style={{ maxHeight: 360, overflowY: 'auto', padding: 6 }}>
        {list.length === 0 && <div className="empty"><b>No match</b>Try a page name or a scenario.</div>}
        {list.map((c, n) => (
          <button key={c.id} role="option" aria-selected={n === i} onMouseEnter={() => setI(n)} onClick={c.run} className="row spread"
            style={{ width: '100%', padding: '9px 12px', border: 0, borderRadius: 8, textAlign: 'start', background: n === i ? 'var(--hover)' : 'transparent' }}>
            <span>{c.label}</span><span className="row muted" style={{ gap: 6, fontSize: 12 }}>{c.hint}{n === i && <CornerDownLeft size={13} />}</span>
          </button>
        ))}
      </div>
    </div>
  </>, document.body);
}

export function Toast() {
  const toast = useUi((s) => s.toast);
  if (!toast) return null;
  return <div key={toast.id} role="status" style={{ position: 'fixed', insetInlineStart: '50%', transform: 'translateX(-50%)', bottom: 28, zIndex: 80, background: 'var(--text)', color: 'var(--bg)', padding: '10px 16px', borderRadius: 10, boxShadow: 'var(--shadow-pop)', animation: 'pop var(--t-med) var(--ease)', maxWidth: 'min(520px, calc(100vw - 32px))' }}>{toast.text}</div>;
}
