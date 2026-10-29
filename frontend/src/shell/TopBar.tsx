import { useEffect, useState } from 'react';
import { Activity, Command, FlaskConical, Languages, Monitor, Moon, Sun } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { href, PAGES, type Page, useRoute } from '../lib/route';
import { useTheme, type ThemeChoice } from '../lib/theme';
import { clock } from '../lib/format';

const NAV: Record<Page, [string, string]> = {
  live: ['nav.live', 'Live twin'], incidents: ['nav.incidents', 'Incidents'], savings: ['nav.savings', 'Savings'],
  forecast: ['nav.forecast', 'Forecast and models'], lab: ['nav.lab', 'AI lab'], executive: ['nav.executive', 'Executive'],
};

function Clock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(i); }, []);
  return <span className="num hide-sm" style={{ color: 'var(--text-2)', fontSize: 13 }} title="Africa/Cairo">{clock(now)} <span className="muted">Cairo</span></span>;
}

export function TopBar() {
  const { t, i18n } = useTranslation();
  const route = useRoute();
  const conn = useStore((s) => s.connection);
  const llm = useStore((s) => s.health?.llm);
  const open = useStore((s) => s.incidents.filter((i) => i.state !== 'resolved').length);
  const set = useUi((s) => s.set);
  const { choice, setTheme } = useTheme();
  const cycle: Record<ThemeChoice, ThemeChoice> = { system: 'light', light: 'dark', dark: 'system' };
  const ThemeIcon = choice === 'system' ? Monitor : choice === 'light' ? Sun : Moon;
  const live = conn === 'connected';

  return (
    <header className="topbar">
      <a className="brand" href={href('live')} style={{ textDecoration: 'none', color: 'inherit' }}>
        <span className="brand-mark"><Activity size={18} strokeWidth={2.6} /></span>
        <span className="brand-text"><div className="brand-name">{t('app.name', 'UtilityGuard')}</div><div className="brand-sub">{t('app.sub', 'New Administrative Capital')}</div></span>
      </a>
      <nav className="nav" aria-label="Sections">
        {PAGES.map((p) => (
          <a key={p} href={href(p)} aria-current={route.page === p ? 'page' : undefined}>
            {t(NAV[p][0], NAV[p][1])}
            {p === 'incidents' && open > 0 && <span className="chip crit num" style={{ height: 18, padding: '0 6px' }}>{open}</span>}
          </a>
        ))}
      </nav>
      <div className="topbar-right">
        <span className="pill" role="status" title={live ? 'Receiving a reading every 2 seconds' : 'Connection lost, retrying'}>
          <i className={`dot ${live ? 'live' : ''}`} style={live ? undefined : { color: 'var(--warn)' }} />{live ? t('top.live', 'Live') : t('top.reconnecting', 'Reconnecting')}
        </span>
        <span className="pill hide-sm" title={llm === 'live' ? 'Agents answer from the live model' : 'Agents answer from recorded responses'}>
          {t('top.agents', 'Agents')}: <b style={{ fontWeight: 600, color: 'var(--text)' }}>{llm === 'fallback' ? 'recorded' : 'live'}</b>
        </span>
        <Clock />
        <button className="btn ghost icon" onClick={() => setTheme(cycle[choice])} aria-label={`Theme: ${choice}`} title={`Theme: ${choice} (click to change)`}><ThemeIcon size={18} /></button>
        <button className="btn ghost icon hide-sm" onClick={() => void i18n.changeLanguage(i18n.language === 'ar' ? 'en' : 'ar')} aria-label="Language" title="English / العربية"><Languages size={18} /></button>
        <button className="btn ghost icon hide-sm" onClick={() => set({ paletteOpen: true })} aria-label={t('top.commands', 'Commands')} title="Ctrl K"><Command size={17} /></button>
        <button className="btn primary" onClick={() => set({ scenarioOpen: true })}><FlaskConical size={16} />{t('top.scenario', 'Scenario lab')}</button>
      </div>
    </header>
  );
}
