import { useEffect, useRef, useState } from 'react';
import { CircleCheck, CircleX, Copy, Info, Send, TriangleAlert } from 'lucide-react';
import { sendBriefing, useBriefing, useBriefingChannels } from '../api/briefing';
import type { Briefing, BriefingDelivery } from '../api/types';
import { briefingSeverity, firstDelivery, orderSections } from '../lib/briefing';
import { href, parse } from '../lib/route';
import { Card, Seg, Skeleton } from './index';
import './Briefing.css';

function BriefingContent({ lang }: { lang: Briefing['lang'] }) {
  const briefing = useBriefing(lang);
  const channels = useBriefingChannels();
  const [busy, setBusy] = useState(false);
  const [delivery, setDelivery] = useState<BriefingDelivery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState('');
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const data = briefing.data;
  const telegram = channels.data?.find((channel) => channel.channel === 'telegram');
  const shown = delivery ?? firstDelivery(data?.deliveries ?? []);
  const send = async () => {
    if (pending.current || !data) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(null); setDelivery(null);
    try {
      const result = await sendBriefing(lang, 'telegram', AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]));
      if (!controller.signal.aborted) setDelivery(result.delivery);
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not send the briefing.');
    } finally {
      if (!controller.signal.aborted) { setBusy(false); briefing.reload(); channels.reload(); }
      pending.current = null;
    }
  };
  const copy = async () => {
    if (!data) return;
    try { await navigator.clipboard.writeText(data.text); setCopyState('Copied'); }
    catch { setCopyState('Copy failed. Please try again.'); }
  };
  return <div className="briefing-content stack">
    {briefing.error && <div className="stack"><p className="briefing-error" role="alert">Could not load the briefing: {briefing.error}</p><div><button className="btn sm" onClick={briefing.reload}>Retry briefing</button></div></div>}
    {!data && briefing.loading && <div role="status" aria-label="Loading the morning briefing"><Skeleton h={24} w="70%" /><Skeleton h={180} /></div>}
    {data && <>
      <div className="briefing-block stack" dir={lang === 'ar' ? 'rtl' : 'ltr'} lang={lang}>
        <h2 className="briefing-headline">{data.headline}</h2>
        {orderSections(data.sections).map((section) => <section key={section.id} className="briefing-section">
          <h3>{section.title}</h3>
          <ul>{section.items.map((item) => {
            const severity = briefingSeverity(item.severity);
            const Icon = item.severity === 'critical' ? CircleX : item.severity === 'warning' ? TriangleAlert : Info;
            const content = <><span className={`chip briefing-severity ${severity.cls}`} lang="en"><Icon size={14} aria-hidden /><span className="sr-only">{severity.label}: </span></span><span>{item.text}</span></>;
            const route = item.link ? parse(item.link) : null;
            return <li key={item.fingerprint}>{route ? <a className="briefing-item" href={href(route.page, route.id)}>{content}</a> : <div className="briefing-item">{content}</div>}</li>;
          })}</ul>
        </section>)}
      </div>
      {data.repeated > 0 && <p className="muted">{data.repeated} repeated item{data.repeated === 1 ? '' : 's'}.</p>}
      <div className="row wrap briefing-actions">
        <button className="btn sm" disabled={busy} onClick={() => void send()}><Send size={14} aria-hidden />{busy ? 'Sending…' : 'Send to Telegram'}</button>
        <button className="btn sm" onClick={() => void copy()}><Copy size={14} aria-hidden />Copy text</button>
        {copyState && <span className="muted" role="status">{copyState}</span>}
      </div>
      <details className="briefing-figures"><summary>Every figure in this briefing</summary><dl>{data.figures.map((figure) => <div key={figure.key}><dt>{figure.label}</dt><dd className="num"><bdi>{figure.display}</bdi></dd></div>)}</dl></details>
    </>}
    {shown && <p className="briefing-delivery" role="status"><span className={`chip ${shown.state === 'sent' ? 'ok' : shown.state === 'failed' ? 'crit' : 'warn'}`}>{shown.state === 'sent' ? <CircleCheck size={13} aria-hidden /> : shown.state === 'failed' ? <CircleX size={13} aria-hidden /> : <Info size={13} aria-hidden />}{shown.channel === 'telegram' ? 'Telegram' : 'In app'}: {shown.state}</span><span>{shown.detail}</span></p>}
    {error && <p className="briefing-error" role="alert">{error}</p>}
    <div className="briefing-channel">
      {telegram ? <><span className={`chip ${telegram.configured ? 'ok' : 'warn'}`}>{telegram.configured ? <CircleCheck size={13} aria-hidden /> : <Info size={13} aria-hidden />}Telegram {telegram.configured ? 'configured' : 'not configured'}</span><p className="muted">{telegram.detail}</p></>
        : channels.loading ? <p className="muted" role="status">Loading Telegram channel status…</p> : !channels.error && <p className="muted">Telegram channel status unavailable.</p>}
      {channels.error && <><p className="briefing-error" role="alert">Could not load channel status: {channels.error}</p><button className="btn sm" onClick={channels.reload}>Retry channel status</button></>}
    </div>
  </div>;
}

export function BriefingCard() {
  const [lang, setLang] = useState<Briefing['lang']>('en');
  return <Card title="Morning briefing" className="briefing" right={<Seg value={lang} onChange={setLang} label="Briefing language" options={[{ v: 'en', label: 'English' }, { v: 'ar', label: 'العربية' }]} />}>
    <BriefingContent key={lang} lang={lang} />
  </Card>;
}
