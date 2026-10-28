import { useEffect, useState } from 'react';
import { Activity, Command, FlaskConical, Languages, Radio } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { Badge, Button, StatusDot, Tooltip } from '../design/primitives';

export function Header({ onScenario, onCommands }: { onScenario: () => void; onCommands: () => void }) {
  const { t, i18n } = useTranslation();
  const connection = useStore((s) => s.connection);
  const health = useStore((s) => s.health);
  const [now, setNow] = useState(new Date());
  useEffect(() => { const timer = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(timer); }, []);
  return <header className="top-bar">
    <a className="brand" href="#operations"><span className="brand-mark"><Activity size={23} strokeWidth={1.6} /></span><span><strong>{t('app.name')}<span className="brand-dot">.</span></strong><small>{t('app.subtitle')}</small></span></a>
    <div className="location"><span className="location-dot" /><span>{t('app.region')}<small>{t('app.workspace')}</small></span></div>
    <div className="top-actions"><Tooltip content={connection === 'connected' ? t('connection.connected') : t('connection.waiting')}><StatusDot status={connection === 'connected' ? 'ok' : connection === 'offline' ? 'offline' : 'warn'} label={t(`connection.${connection}`)} /></Tooltip><span className="llm-badge"><Badge tone={health?.llm === 'live' ? 'ok' : health?.llm === 'fallback' ? 'warn' : 'neutral'}><Radio size={11} />{t(`connection.${health?.llm ?? 'unknown'}`)}</Badge></span><time className="clock" dateTime={now.toISOString()}>{now.toISOString().slice(11, 19)}<small>{t('connection.utc')}</small></time><span className="top-divider" /><Button variant="ghost" aria-label={t('commands.open')} onClick={onCommands}><Command size={15} /></Button><Button variant="ghost" aria-label={t('app.language')} onClick={() => void i18n.changeLanguage(i18n.language === 'en' ? 'ar' : 'en')}><Languages size={16} /></Button><Button variant="primary" onClick={onScenario}><FlaskConical size={14} /><span>{t('scenario.title')}</span></Button></div>
  </header>;
}
