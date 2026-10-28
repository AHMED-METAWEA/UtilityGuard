import { AnimatePresence, motion } from 'framer-motion';
import { Bell, Bot, ChevronDown, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { Badge, DataState, Panel, PanelTitle, Tooltip } from '../design/primitives';
import { FindingCard } from './FindingCard';
import { isOpenAlert } from '../presentation';
import { loadResources } from '../api/client';

export function ActivityPanel({ onAssumptions, onCalculation }: { onAssumptions: () => void; onCalculation: (id: string) => void }) {
  const { t } = useTranslation();
  const alerts = useStore((s) => s.alerts);
  const findings = useStore((s) => s.findings);
  const incidents = new Map<string, typeof alerts.data>();
  alerts.data.forEach((alert) => incidents.set(alert.incidentId, [...(incidents.get(alert.incidentId) ?? []), alert]));
  return <Panel className="activity-panel"><PanelTitle title={t('alerts.title')} icon={<Bell size={14} />} meta={<span className="live-tag"><span />{t('alerts.live')}</span>} /><div className="activity-summary"><span>{t('alerts.open')}</span><Tooltip content={t('alerts.open')}><strong className="font-mono">{alerts.status === 'ready' ? alerts.data.filter(isOpenAlert).length.toLocaleString() : t('common.none')}</strong></Tooltip></div><div className="alerts-content"><DataState status={alerts.status} empty={!alerts.data.length} title={t('alerts.empty')} detail={t('alerts.detail')} onRetry={() => void loadResources()}><AnimatePresence initial={false}>{[...incidents].map(([id, items]) => <motion.details key={id} className={`incident incident-${items[0].severity}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}><summary><span><Badge tone={items[0].severity === 'critical' ? 'fault' : items[0].severity === 'warning' ? 'warn' : 'water'}>{t(`alerts.${items[0].severity}`)}</Badge><strong>{items[0].title}</strong></span><ChevronDown size={14} /></summary><span className="incident-label">{t('alerts.incident', { id })}</span>{items.map((alert) => <article key={alert.id}><p>{alert.summary}</p><small>{t(`networks.${alert.network}`)} · {t(`alerts.${alert.state === 'open' ? 'openState' : alert.state}`)}</small></article>)}</motion.details>)}</AnimatePresence></DataState></div><div className="finding-section"><PanelTitle title={t('findings.title')} icon={<Sparkles size={14} />} meta={<Bot size={15} className="text-faint" />} /><DataState status={findings.status} empty={!findings.data.length} title={t('findings.empty')} detail={t('findings.detail')}>{findings.data.map((finding) => <FindingCard key={finding.id} finding={finding} onAssumptions={onAssumptions} onCalculation={onCalculation} />)}</DataState></div></Panel>;
}
