import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Bot, ChevronDown, Send, Sparkles } from 'lucide-react';
import { post } from '../api/client';
import type { Agent, ChatAccepted, ChatMessage } from '../api/types';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { NetIcon } from '../ui';
import { money } from '../lib/useSensorSeries';

const AGENT: Record<Agent, string> = { water: 'Water', electricity: 'Electricity', cooling: 'Cooling', orchestrator: 'Orchestrator' };
const SUGGEST = ['What happens if SS-02 trips?', 'What is the best plan right now and what will it cost?', 'Why is pressure low in the Government District?'];

const AgentChip = ({ a, active, reason }: { a: Agent; active?: boolean; reason?: string }) => (
  <span className="chip" title={reason} style={{ background: active ? 'var(--accent-soft)' : undefined, borderColor: active ? 'var(--accent-border)' : undefined, transition: 'all var(--t-med) var(--ease)' }}>
    {a !== 'orchestrator' ? <NetIcon n={a} size={13} /> : <Bot size={13} />}{AGENT[a]}
  </span>
);

function Bubble({ m }: { m: ChatMessage }) {
  const set = useUi((s) => s.set);
  if (m.role === 'user') return <div style={{ alignSelf: 'flex-end', maxWidth: '85%', background: 'var(--accent)', color: 'var(--on-accent)', padding: '8px 12px', borderRadius: '12px 12px 4px 12px' }}>{m.text}</div>;
  return (
    <div className="stack" style={{ gap: 6, alignSelf: 'stretch' }}>
      {m.routing.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          <span className="muted" style={{ fontSize: 12 }}>Routed to</span>
          {m.routing.map((r, i) => <span key={r.agent} className="row" style={{ gap: 4 }}>{i > 0 && <ArrowRight size={12} color="var(--text-3)" />}<AgentChip a={r.agent} reason={r.reason} active={!m.complete && m.activeAgent === r.agent} /></span>)}
        </div>
      )}
      <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', padding: '10px 12px', borderRadius: '12px 12px 12px 4px', whiteSpace: 'pre-wrap', minHeight: 40 }}>
        {m.text ? money(m.text) : <span className="muted">The team is working on it...</span>}
        {!m.complete && m.text && <span className="muted"> ▍</span>}
      </div>
      {m.complete && (
        <div className="row wrap" style={{ gap: 8, fontSize: 12 }}>
          {m.fallback && <span className="chip warn">Recorded answer</span>}
          {m.calculationId && <button className="btn ghost sm" style={{ height: 24, padding: '0 6px', color: 'var(--accent)' }} onClick={() => set({ calcId: m.calculationId ?? null })}>See the calculation</button>}
        </div>
      )}
    </div>
  );
}

export function ChatDock() {
  const open = useUi((s) => s.chatOpen);
  const set = useUi((s) => s.set); const notify = useUi((s) => s.notify);
  const chat = useStore((s) => s.chat.data);
  const append = useStore((s) => s.appendUserMessage);
  const incidents = useStore((s) => s.incidents);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [chat, open]);

  const send = async (q: string) => {
    const message = q.trim(); if (!message || sending) return;
    setText(''); setSending(true); append(message);
    const inc = incidents.filter((i) => i.state !== 'resolved')[0];
    try { await post<ChatAccepted>('/chat', { message, incidentId: inc?.id }); }
    catch (e) { notify(`The team could not be reached: ${(e as Error).message}`); }
    finally { setSending(false); }
  };

  if (!open) {
    return <button className="btn primary no-print" onClick={() => set({ chatOpen: true })} style={{ position: 'fixed', insetInlineEnd: 24, bottom: 24, zIndex: 40, height: 44, borderRadius: 22, padding: '0 18px', boxShadow: 'var(--shadow-pop)' }}><Sparkles size={16} />Ask the team</button>;
  }
  return (
    <section className="card no-print" aria-label="Ask the team" style={{ position: 'fixed', insetInlineEnd: 24, bottom: 24, zIndex: 40, width: 'min(420px, calc(100vw - 32px))', height: 'min(620px, calc(100vh - 96px))', display: 'flex', flexDirection: 'column', boxShadow: 'var(--shadow-pop)' }}>
      <header className="row spread" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
        <div><div className="card-title">Ask the team</div><div className="card-sub">The orchestrator routes your question to the right agents</div></div>
        <button className="btn ghost icon sm" aria-label="Minimise" onClick={() => set({ chatOpen: false })}><ChevronDown size={18} /></button>
      </header>
      <div className="stack" style={{ flex: 1, overflowY: 'auto', padding: 16, gap: 14 }}>
        {chat.length === 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <div className="sub">Ask about an asset, a district or an open incident. Every figure in an answer comes from the cost model.</div>
            {SUGGEST.map((s) => <button key={s} className="btn" style={{ justifyContent: 'flex-start', height: 'auto', padding: '8px 12px', textAlign: 'start', whiteSpace: 'normal' }} onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {chat.map((m) => <Bubble key={m.messageId} m={m} />)}
        <div ref={end} />
      </div>
      <form className="row" style={{ padding: 12, borderTop: '1px solid var(--border)' }} onSubmit={(e) => { e.preventDefault(); void send(text); }}>
        <input className="field" placeholder="Ask about your district..." value={text} onChange={(e) => setText(e.target.value)} aria-label="Your question" />
        <button className="btn primary icon" type="submit" aria-label="Send" disabled={!text.trim() || sending}><Send size={16} /></button>
      </form>
      <footer style={{ padding: '0 12px 10px' }}><button className="btn ghost sm" onClick={() => set({ guardrail: {} })}>Test the AI</button></footer>
    </section>
  );
}
