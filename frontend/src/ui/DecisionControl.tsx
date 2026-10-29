import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Check, Copy, Loader, Scale, TriangleAlert } from 'lucide-react';
import { post, request } from '../api/client';
import type { BoardDecision, OrderPreview, ReviewRequest } from '../api/types';
import { cairoDateTime, shortHash } from '../lib/audit';
import { canApplyReview, moveOrder, proposalOrder, reviewError } from '../lib/decision';
import { fmt } from '../lib/format';
import { href } from '../lib/route';
import { useStore } from '../store';

const OPERATOR_KEY = 'utilityguard.operator';
const rememberedOperator = () => { try { return (localStorage.getItem(OPERATOR_KEY) ?? '').slice(0, 60); } catch { return ''; } };

export function DecisionControl({ decision, onLatest }: { decision: BoardDecision; onLatest: () => void }) {
  const status = decision.status ?? 'proposed';
  const [operator, setOperator] = useState(rememberedOperator);
  const [note, setNote] = useState('');
  const [reason, setReason] = useState('');
  const [rejecting, setRejecting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [order, setOrder] = useState(() => proposalOrder(decision));
  const [preview, setPreview] = useState<{ key: string; value: OrderPreview } | null>(null);
  const [previewError, setPreviewError] = useState<{ key: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState('Copy fingerprint');
  const submitting = useRef(false);
  const mounted = useRef(true);
  const key = JSON.stringify(order);
  const currentPreview = preview?.key === key ? preview.value : null;
  const currentPreviewError = previewError?.key === key ? previewError.message : null;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!editing || status !== 'proposed') return;
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      // A cancelled effect never installs an older price over a newer order.
      void request<OrderPreview>(`/board/sessions/${encodeURIComponent(decision.sessionId)}/preview`, {
        method: 'POST', body: JSON.stringify({ order: JSON.parse(key) }), signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]),
      }).then((value) => { if (active) { setPreview({ key, value }); setPreviewError(null); } })
        .catch((e: unknown) => { if (active) setPreviewError({ key, message: reviewError(e) }); });
    }, 300);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [editing, status, decision.sessionId, key]);

  const decide = async (outcome: 'approve' | 'reject') => {
    if (submitting.current || !operator.trim() || operator.trim().length > 60 || (outcome === 'reject' && reason.trim().length < 3) || (outcome === 'approve' && editing && !currentPreview)) return;
    submitting.current = true; setBusy(true); setError(null);
    const revision = useStore.getState().auditRevision;
    const body: ReviewRequest = { operator: operator.trim(), note: outcome === 'reject' ? reason.trim() : note.trim(), ...(outcome === 'approve' && editing ? { order } : {}) };
    try {
      const updated = await post<BoardDecision>(`/board/sessions/${encodeURIComponent(decision.sessionId)}/${outcome}`, body);
      const store = useStore.getState(), current = store.boards[decision.sessionId]?.decision;
      // A reset removes the session; a newer WS decision takes precedence over this reply.
      if (canApplyReview(decision, current, revision, store.auditRevision)) store.setBoardDecision(updated);
    } catch (e) { if (mounted.current) setError(reviewError(e)); }
    finally { submitting.current = false; if (mounted.current) setBusy(false); }
  };
  const review = decision.review;
  const finished = (decision.execution ?? []).filter((step) => step.state === 'done' || step.state === 'skipped').length;
  const Icon = status === 'completed' ? Check : status === 'executing' ? Loader : status === 'proposed' ? Scale : TriangleAlert;
  return <section className="decision-control stack" aria-label="Decision" style={{ gap: 12 }}>
    <h3 className="card-title" style={{ margin: 0 }}>Decision</h3>
    <div className={`decision-banner decision-${status}`} role="status">
      <Icon size={17} aria-hidden /><div><b>{status.charAt(0).toUpperCase() + status.slice(1)}</b>
        <p>{status === 'proposed' ? 'Waiting for a person to decide. The board changes nothing by itself.'
          : status === 'executing' ? `Crews are carrying out the approved plan. ${finished} of ${decision.execution.length} steps finished.`
          : status === 'completed' ? 'The approved crew work is complete. Detector confirmations are shown below.'
          : status === 'rejected' ? `${review?.operator ?? 'An operator'} rejected this plan${review?.note ? `: ${review.note}` : '.'}`
          : status === 'superseded' ? 'A newer proposal replaced this one.' : 'Everything it covered had already cleared.'}</p>
        {status === 'executing' && <progress aria-label="Execution progress" max={Math.max(1, decision.execution.length)} value={finished} />}
        {status === 'superseded' && <button className="btn sm" type="button" onClick={onLatest}>Open latest proposal</button>}
      </div>
    </div>
    {review && <div className="decision-review stack" style={{ gap: 6 }}>
      <div><b>{review.outcome === 'approved' ? 'Approved' : 'Rejected'}</b> by {review.operator} · <time dateTime={review.at}>{cairoDateTime(review.at)} Cairo</time></div>
      {review.outcome === 'approved' && <div>{review.override ? <>Their own order · Override · <span className="num">{fmt(review.costDelta, 2)} EGP</span> cost delta</> : "The board's plan"}</div>}
      {review.note && review.outcome === 'approved' && <p style={{ margin: 0 }}>{review.note}</p>}
      <div className="row wrap" style={{ gap: 8 }}>
        <span title="This fingerprint identifies exactly the proposal the person saw when they decided.">Decision fingerprint <code title={review.decisionHash}>{shortHash(review.decisionHash)}</code></span>
        <button className="btn sm" type="button" title="Copy the SHA-256 fingerprint of exactly what the person saw" onClick={() => {
          void (async () => { try { await navigator.clipboard.writeText(review.decisionHash); setCopyState('Copied'); } catch { setCopyState('Copy failed'); } })();
        }}><Copy size={13} aria-hidden />{copyState}</button><span className="sr-only" role="status">{copyState}</span>
      </div>
    </div>}
    {status === 'proposed' && <>
      <label className="stack" style={{ gap: 4 }}>Operator name <input className="field" required minLength={1} maxLength={60} autoComplete="name" value={operator} disabled={busy} onChange={(e) => {
        setOperator(e.target.value); try { localStorage.setItem(OPERATOR_KEY, e.target.value); } catch { /* Storage may be unavailable. */ }
      }} /></label>
      <label className="stack" style={{ gap: 4 }}>Note (optional) <textarea className="field" rows={2} maxLength={500} value={note} disabled={busy} onChange={(e) => setNote(e.target.value)} /></label>
      {editing && <div className="decision-order stack" style={{ gap: 10 }}>
        <h4 style={{ margin: 0 }}>Your start order</h4>
        <ol>{order.map((id, index) => <li key={id}><span>{decision.tasks.find((task) => task.id === id)?.assetName ?? id}</span><div className="row" style={{ gap: 4 }}>
          <button type="button" className="btn sm" disabled={busy || index === 0} aria-label={`Move ${decision.tasks.find((task) => task.id === id)?.assetName ?? id} up`} onClick={() => { setPreview(null); setPreviewError(null); setOrder(moveOrder(order, index, -1)); }}><ArrowUp size={14} aria-hidden /></button>
          <button type="button" className="btn sm" disabled={busy || index === order.length - 1} aria-label={`Move ${decision.tasks.find((task) => task.id === id)?.assetName ?? id} down`} onClick={() => { setPreview(null); setPreviewError(null); setOrder(moveOrder(order, index, 1)); }}><ArrowDown size={14} aria-hidden /></button>
        </div></li>)}</ol>
        <div aria-live="polite">{currentPreview ? <><div>Expected cost: <b className="num">{fmt(currentPreview.expectedCost, 2)} EGP</b> · Board: <span className="num">{fmt(currentPreview.boardCost, 2)} EGP</span></div><div>{!currentPreview.override ? "Same schedule as the board's" : currentPreview.costDelta >= 0 ? `Costs ${fmt(currentPreview.costDelta, 2)} EGP more than the board's plan` : `Costs ${fmt(Math.abs(currentPreview.costDelta), 2)} EGP less than the board's plan`}</div></> : currentPreviewError ? <span role="alert">{currentPreviewError}</span> : 'Pricing this order…'}</div>
        {currentPreview && <ul className="board-list">{currentPreview.steps.map((step) => <li key={step.taskId}>{step.assetName}: {step.dropped ? 'No crew needed' : `Crew ${String.fromCharCode(64 + step.crewSlot)} · ${fmt(step.startHour, 1)}–${fmt(step.endHour, 1)} h`}</li>)}</ul>}
      </div>}
      {rejecting && <label className="stack" style={{ gap: 4 }}>Reason for rejection (required, at least 3 characters)<textarea autoFocus className="field" rows={2} required minLength={3} maxLength={500} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>}
      <div className="row wrap" style={{ gap: 8 }}>
        <button className="btn primary" type="button" disabled={busy || !operator.trim() || (editing && !currentPreview)} onClick={() => void decide('approve')}>{busy ? 'Submitting…' : editing ? 'Approve my order' : 'Approve plan'}</button>
        <button className="btn" type="button" disabled={busy} aria-expanded={editing} onClick={() => { setEditing(!editing); setRejecting(false); }}>{editing ? "Use the board's plan" : 'Change the order'}</button>
        {rejecting ? <><button className="btn danger" type="button" disabled={busy || !operator.trim() || reason.trim().length < 3} onClick={() => void decide('reject')}>Confirm rejection</button><button className="btn" type="button" disabled={busy} onClick={() => setRejecting(false)}>Cancel</button></>
          : <button className="btn" type="button" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>}
      </div>
    </>}
    {error && <p className="decision-error" role="alert">{error}</p>}
    <a href={href('audit', decision.sessionId)}>Open the decision log</a>
  </section>;
}
