import { useState } from 'react';
import { motion } from 'framer-motion';
import { Bot, Download, FileText, SlidersHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AgentFinding, Calculation, Cost, Explanation } from '../api/types';
import { useStore } from '../store';
import { Badge, Button, NumberValue } from '../design/primitives';
import { substitutedFormula, keyCalculations } from '../presentation';

export function CalculationView({ calculation }: { calculation: Calculation }) {
  const { t } = useTranslation();
  return <article className="calculation"><strong>{calculation.name}</strong><code>{calculation.formula}</code><p className="formula-values">{substitutedFormula(calculation)} = <b title={calculation.unit}>{calculation.result.toLocaleString('en', { maximumFractionDigits: 2 })} {calculation.unit}</b></p><details><summary>{t('decision.inputs')}</summary>{calculation.inputs.map((input) => <small key={input.name} title={input.source}>{input.name}: {input.value.toLocaleString()} {input.unit} · {input.source}</small>)}</details></article>;
}

export function CostTiles({ cost }: { cost: Cost }) {
  const { t } = useTranslation();
  return <div className="cost-tiles">{[
    { label: t('decision.actNow'), value: cost.actNow },
    { label: t('decision.delay', { hours: cost.delayHours }), value: cost.costOfDelay },
    { label: t('decision.saving'), value: cost.expectedSaving, saving: true },
  ].map((tile) => <div key={tile.label} className={tile.saving ? 'saving-tile' : ''}><span>{tile.label}</span><NumberValue value={tile.value} unit={cost.currency} digits={2} /></div>)}</div>;
}

export function SensitivityLines({ explain }: { explain: Explanation }) {
  const { t } = useTranslation();
  const keys = [...new Set(explain.sensitivity.map((row) => row.input))];
  return <>{keys.map((key) => { const rows = explain.sensitivity.filter((row) => row.input === key).sort((a, b) => a.changePercent - b.changePercent); return <p key={key} className="sensitivity">{t('decision.sensitivity', { name: explain.assumptions.find((a) => a.key === key)?.name ?? key, changes: rows.map((r) => `${r.changePercent > 0 ? '+' : ''}${r.changePercent}%`).join(' / '), savings: rows.map((r) => r.newSaving.toLocaleString()).join('–') })}</p>; })}</>;
}

export function FindingCard({ finding, onAssumptions, onCalculation }: { finding: AgentFinding; onAssumptions: () => void; onCalculation: (id: string) => void }) {
  const { t } = useTranslation();
  const alerts = useStore((s) => s.alerts.data);
  const incident = useStore((s) => s.incidents.find((i) => i.id === finding.incidentId));
  const severity = incident?.severity ?? alerts.find((a) => a.incidentId === finding.incidentId)?.severity ?? 'info';
  const [all, setAll] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  async function download() {
    setDownloading(true); setError('');
    try {
      const response = await fetch(`/api/v1/incidents/${encodeURIComponent(finding.incidentId)}/report`, { signal: AbortSignal.timeout(15000) });
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.message ?? t('common.unavailable')); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `${finding.incidentId}.pdf`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err instanceof Error ? err.message : t('common.unavailable')); }
    finally { setDownloading(false); }
  }
  return <article className={`finding decision-card decision-${severity}`}>
    <div className="decision-meta"><Badge tone={severity === 'critical' ? 'fault' : severity === 'warning' ? 'warn' : 'water'}>{t(`alerts.${severity}`)}</Badge><span title={t('decision.confidence')}>{Math.round(finding.explain.confidence * 100)}% · <time dateTime={finding.createdAt}>{new Date(finding.createdAt).toLocaleTimeString()}</time></span></div>
    <h3>{finding.headline}</h3><div className="agent-chip"><Bot size={14} />{t('decision.agent', { agent: t(`networks.${finding.agent}`) })}</div><p>{finding.explanation}</p>
    <CostTiles cost={finding.cost} /><h4>{t('decision.why')}</h4><ol className="decision-reasons">{finding.explain.reasons.map((reason, i) => <li key={`${i}-${reason.text}`}><span>{reason.text} <span className={`evidence evidence-${reason.evidenceType}`} title={reason.evidenceRef}>{t(`evidence.${reason.evidenceType}`)}</span></span></li>)}</ol>
    <h4>{t('decision.calculations')}</h4>{(all ? finding.explain.calculations : keyCalculations(finding.explain.calculations)).map((calculation) => <CalculationView key={calculation.name} calculation={calculation} />)}
    {finding.explain.calculations.length > 3 && <Button variant="ghost" aria-expanded={all} onClick={() => setAll(!all)}>{t(all ? 'decision.fewer' : 'decision.all')}</Button>}
    <SensitivityLines explain={finding.explain} />
    <div className="recommended"><h4>{t('decision.action')}</h4><p>{finding.recommendedAction.text}</p><Badge tone="warn">{t(`urgency.${finding.recommendedAction.urgency}`)}</Badge> <Badge>{finding.recommendedAction.crewType}</Badge></div>
    <details className="reasoning" open><summary>{t('decision.steps')}</summary><ol key={JSON.stringify(finding)}>{finding.reasoningSteps.map((step, i) => <motion.li key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.22, duration: 0.2 }}>{step}</motion.li>)}</ol></details>
    <div className="decision-badges">{finding.fallback && <Badge tone="warn">{t('decision.recorded')}</Badge>}<Badge tone={finding.numberCheck === 'passed' ? 'ok' : 'warn'}>{t(finding.numberCheck === 'passed' ? 'decision.verified' : 'decision.corrected')}</Badge></div>
    <div className="decision-actions"><Button variant="primary" onClick={() => onCalculation(finding.explain.calculationId)}><FileText size={13} />{t('decision.open')}</Button><Button disabled={downloading} onClick={() => void download()}><Download size={13} />{t(downloading ? 'decision.downloading' : 'decision.download')}</Button><Button variant="ghost" onClick={onAssumptions}><SlidersHorizontal size={13} />{t('decision.assumptions')}</Button></div>{error && <p role="alert" className="inline-error">{error}</p>}
  </article>;
}
