import { useEffect, useRef, useState } from 'react';
import { CircleCheck, CircleX, FlaskConical, ShieldCheck } from 'lucide-react';
import { request, RequestError } from '../api/client';
import type { GuardrailResult } from '../api/types';
import { PRESETS, segment, summarize } from '../lib/guardrail';
import { useUi } from '../store/ui';
import { Card, Drawer, Skeleton } from './index';
import './Guardrail.css';

function Result({ result }: { result: GuardrailResult }) {
  return <section className="stack" aria-label="Number checker result">
    {result.source === 'llm' ? <span className="chip accent guardrail-source">Model answer</span>
      : <div className="guardrail-planted"><FlaskConical size={17} aria-hidden /><span>No model answered, so a draft with invented figures stands in to show the check</span></div>}
    <div className="guardrail-panels">
      <Card title="What the model wrote" sub={result.source === 'planted' ? 'Stand-in draft · no model answered' : 'Before the number check'}>
        <p className="guardrail-answer">{segment(result.raw, result.figures).map((piece, i) => piece.figure
          ? <mark key={i} className={`guardrail-figure ${piece.figure.verdict}`} title={piece.figure.verdict === 'verified' ? `matches ${piece.figure.matched ?? 'calculated value'} (${piece.figure.matchedValue ?? piece.figure.value})` : 'Removed: not supported by the calculated values'}><span className="sr-only">{piece.figure.verdict}: </span>{piece.text}</mark>
          : <span key={i}>{piece.text}</span>)}</p>
      </Card>
      <Card title="What the user would see" sub="After the same check used for agent answers">
        <p className="guardrail-answer">{result.checked.split(/(\[figure removed\])/g).map((piece, i) => piece === '[figure removed]'
          ? <span key={i} className="guardrail-figure removed-placeholder">{piece}</span> : <span key={i}>{piece}</span>)}</p>
      </Card>
    </div>
    <p className="guardrail-summary" role="status">{summarize(result)}</p>
    {result.removed === 0 && <p className="guardrail-good"><CircleCheck size={16} aria-hidden /><span>The model did not repeat the claim, so there was nothing to remove. That is a good outcome: the answer stayed grounded in the data.</span></p>}
    <table className="table guardrail-table"><caption className="sr-only">Each figure and the number checker’s verdict</caption><thead><tr><th>Figure</th><th>Verdict</th><th>Matched calculated value</th></tr></thead><tbody>
      {result.figures.map((figure, i) => <tr key={i}><td className="num">{figure.text}</td><td><span className={`chip ${figure.verdict === 'verified' ? 'ok' : 'crit'}`}>{figure.verdict === 'verified' ? <CircleCheck size={13} aria-hidden /> : <CircleX size={13} aria-hidden />}{figure.verdict === 'verified' ? 'Verified' : 'Removed'}</span></td><td>{figure.matched ?? 'No match'}{figure.matchedValue != null && <span className="num"> ({figure.matchedValue})</span>}</td></tr>)}
      {!result.figures.length && <tr><td colSpan={3} className="muted">No figures to check.</td></tr>}
    </tbody></table>
  </section>;
}

function Probe({ incidentId }: { incidentId?: string }) {
  const [prompt, setPrompt] = useState(PRESETS[0]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<GuardrailResult | null>(null);
  const [error, setError] = useState<{ message: string; noIncident: boolean } | null>(null);
  const pending = useRef<AbortController | null>(null);
  const set = useUi((s) => s.set);
  useEffect(() => () => pending.current?.abort(), []);
  const run = async () => {
    if (pending.current || !prompt.trim()) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await request<GuardrailResult>('/guardrail/probe', {
        method: 'POST', body: JSON.stringify({ prompt: prompt.trim(), ...(incidentId ? { incidentId } : {}) }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(60000)]),
      });
      if (!controller.signal.aborted) setResult(data);
    } catch (e) {
      if (!controller.signal.aborted) setError({ message: e instanceof Error ? e.message : 'Could not run the test.', noIncident: e instanceof RequestError && e.code === 'no_incident' });
    } finally { if (!controller.signal.aborted) setBusy(false); pending.current = null; }
  };
  return <div className="guardrail-content stack">
    <p className="sub">Send a claim the data does not support. The model answers; the same number checker that protects every agent answer then marks each figure.</p>
    <div className="guardrail-presets" role="group" aria-label="Preset claims">{PRESETS.map((preset) => <button key={preset} className="btn sm" disabled={busy} onClick={() => { setPrompt(preset); setResult(null); setError(null); }}>{preset}</button>)}</div>
    <form className="stack" onSubmit={(event) => { event.preventDefault(); void run(); }}>
      <label className="stack" style={{ gap: 4 }}>Your claim<textarea className="field" rows={3} required maxLength={600} value={prompt} disabled={busy} aria-describedby="guardrail-counter" onChange={(event) => { setPrompt(event.target.value); setResult(null); setError(null); }} /></label>
      <div className="row spread" style={{ gap: 8 }}><span id="guardrail-counter" className="muted num">{prompt.length} / 600 characters</span><button className="btn primary" disabled={busy || !prompt.trim()} type="submit"><ShieldCheck size={16} aria-hidden />{busy ? 'Checking…' : 'Run'}</button></div>
    </form>
    {busy && <div className="stack" role="status" aria-label="Running the guardrail test"><span className="muted">Getting an answer and checking its figures…</span><Skeleton h={22} w="65%" /><div className="guardrail-panels"><Skeleton h={160} /><Skeleton h={160} /></div></div>}
    {error && <div className="stack"><p className="guardrail-error" role="alert">{error.noIncident ? 'Run a scenario first: the test needs an incident to check the answer against.' : error.message}</p><div>{error.noIncident
      ? <button className="btn" onClick={() => set({ guardrail: null, scenarioOpen: true })}><FlaskConical size={16} aria-hidden />Open the Scenario Lab</button>
      : <button className="btn" onClick={() => void run()}>Retry</button>}</div></div>}
    {result && <Result result={result} />}
  </div>;
}

export function Guardrail() {
  const context = useUi((s) => s.guardrail);
  if (!context) return null;
  return <Drawer modal width={960} title="Try to fool the AI" onClose={() => useUi.getState().set({ guardrail: null })}><Probe key={context.incidentId ?? 'latest'} incidentId={context.incidentId} /></Drawer>;
}
