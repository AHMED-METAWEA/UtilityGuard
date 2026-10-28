import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, ChevronDown, ChevronUp, MessageSquare, Network } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { request } from '../api/client';
import type { ChatAccepted } from '../api/types';
import { Badge, Button, DataState, Panel } from '../design/primitives';

export function ChatPanel({ onCalculation }: { onCalculation: (id: string) => void }) {
  const { t } = useTranslation();
  const chat = useStore((s) => s.chat);
  const incidentId = useStore((s) => s.findings.data[0]?.incidentId ?? s.alerts.data.find((a) => a.state !== 'resolved')?.incidentId);
  const activeRequest = useRef<string | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<'error' | 'timeout' | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); if (activeRequest.current && chat.data.some((m) => m.messageId === activeRequest.current && m.complete)) setPending(false); }, [chat.data]);
  useEffect(() => { if (!pending) return; const timer = setTimeout(() => { setPending(false); setError('timeout'); }, 30_000); return () => clearTimeout(timer); }, [pending]);
  async function send(question = draft) {
    if (!question.trim() || pending) return;
    const message = question.trim(); activeRequest.current = null; setDraft(''); setError(null); setPending(true);
    useStore.getState().appendUserMessage(message);
    try { const accepted = await request<ChatAccepted>('/chat', { method: 'POST', body: JSON.stringify({ message, ...(incidentId ? { incidentId } : {}) }) }); activeRequest.current = accepted.messageId; if (useStore.getState().chat.data.some((m) => m.messageId === accepted.messageId && m.complete)) setPending(false); }
    catch { setPending(false); setError('error'); setDraft(message); }
  }
  return <Panel className={`chat-panel ${expanded ? 'expanded' : ''}`}><button className="chat-heading" aria-expanded={expanded} aria-controls="chat-body" onClick={() => setExpanded(!expanded)}><span className="chat-logo"><MessageSquare size={16} /></span><span><strong>{t('chat.title')}</strong><small>{t('chat.subtitle')}</small></span>{expanded ? <ChevronDown size={15} /> : <ChevronUp size={15} />}</button>{expanded && <div id="chat-body"><div className="chat-messages" aria-live="polite" aria-relevant="additions text"><DataState status={chat.status} empty={!chat.data.length} title={t('chat.empty')} detail={t('chat.detail')}>{chat.data.map((message) => <article key={message.messageId} className={`chat-message message-${message.role}`}><small>{message.role === 'user' ? t('chat.you') : t('networks.orchestrator')}</small>{message.routing.length > 0 && <div className="routing"><Network size={12} />{t('chat.routing')}{message.routing.map((route, i) => <span key={i} className={!message.complete && message.activeAgent === route.agent ? 'route-active' : ''} title={route.reason}>{i > 0 ? '→ ' : ''}{t(`networks.${route.agent}`)}</span>)}</div>}<p>{message.text}</p>{message.agentsConsulted.length > 0 && <small>{t('chat.consulted')}: {message.agentsConsulted.map((agent) => t(`networks.${agent}`)).join(', ')}</small>}{!message.complete && message.activeAgent && <small className="agent-answering route-active">{t('chat.answering', { agent: t(`networks.${message.activeAgent}`) })}</small>}{message.calculationId && <Button variant="ghost" onClick={() => onCalculation(message.calculationId!)}>{t('chat.calculation')}</Button>}{message.fallback && <Badge tone="warn">{t('chat.fallback')}</Badge>}</article>)}</DataState>{pending && <div className="reply-pending"><span className="skeleton" />{t('chat.sending')}</div>}<div ref={end} /></div>{error && <p className="chat-error" role="alert">{t(`chat.${error}`)}</p>}<div className="suggested-questions">{(t('chat.suggestions', { returnObjects: true }) as string[]).map((question) => <button key={question} disabled={pending} onClick={() => void send(question)}>{question}</button>)}</div><form className="chat-compose" onSubmit={(event) => { event.preventDefault(); void send(); }}><input value={draft} onChange={(event) => setDraft(event.target.value)} aria-label={t('chat.label')} placeholder={t('chat.placeholder')} maxLength={4000} /><Button type="submit" variant="primary" aria-label={t('chat.send')} disabled={!draft.trim() || pending}><ArrowUpRight size={18} /></Button></form><div className="chat-footer"><span className="small-orbit" />{t('networks.orchestrator')}</div></div>}</Panel>;
}
