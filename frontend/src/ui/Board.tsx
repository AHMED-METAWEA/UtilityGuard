import { Fragment, useEffect, useId, useRef, useState } from 'react';
import { Check, Clock3, Scale, TriangleAlert, Zap } from 'lucide-react';
import { post, request, RequestError } from '../api/client';
import type { BoardDecision, BoardMessage, BoardSession } from '../api/types';
import { ganttLayout } from '../charts/gantt';
import { useWidth } from '../charts/scale';
import { cairoDateTime } from '../lib/audit';
import { planHourNow, scheduledSteps } from '../lib/decision';
import { DecisionControl } from './DecisionControl';
import { compact, fmt } from '../lib/format';
import { latestBoardFor, useStore } from '../store';
import { useUi } from '../store/ui';
import { Card, Meter, NET, NetIcon, Skeleton, StatusChip } from '.';
import { EvidenceTag } from './Provenance';
import './Board.css';

const ROUNDS = ['Opening', 'Positions', 'What waiting costs', 'Decision', 'Replies'];
const crewName = (slot: number) => String.fromCharCode(64 + slot);
const SourceTag = ({ source }: { source: BoardMessage['source'] }) => <span className="tag">{source === 'llm' ? 'Model wording' : 'System wording'}</span>;

function Transcript({ session }: { session: BoardSession }) {
  const ref = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  useEffect(() => { nearBottom.current = true; }, [session.id]);
  useEffect(() => {
    if (nearBottom.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [session.id, session.messages.length, session.state]);
  return (
    <div ref={ref} className="board-transcript" role="region" aria-label="Board transcript" tabIndex={0}
      onScroll={(e) => { const el = e.currentTarget; nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; }}>
      {session.messages.map((message, i) => (
        <Fragment key={message.id}>
          {(i === 0 || session.messages[i - 1].round !== message.round) && <div className="board-round">{ROUNDS[message.round]}</div>}
          <article className="board-message" style={{ animation: 'fade var(--t-med) var(--ease)' }}>
            <div className="row wrap" style={{ gap: 7 }}>
              {message.agent === 'orchestrator' ? <Scale size={16} color="var(--accent)" aria-hidden /> : <NetIcon n={message.agent} size={16} />}
              <b>{message.agent === 'orchestrator' ? 'Chair' : `${NET[message.agent].label} agent`}</b>
              <SourceTag source={message.source} />
            </div>
            <p>{message.text}</p>
            <details>
              <summary className="muted">Figures</summary>
              <dl className="board-facts">
                {message.facts.map((fact, j) => <div key={j}>
                  <dt>{fact.label}</dt>
                  <dd><span className="num" title={`${fmt(fact.value, 2)} ${fact.unit}`}>{fmt(fact.value, fact.value % 1 ? 2 : 0)} <span className="muted">{fact.unit}</span></span><EvidenceTag e={fact.source} /></dd>
                </div>)}
                {!message.facts.length && <div className="muted">No figures in this message.</div>}
              </dl>
            </details>
          </article>
        </Fragment>
      ))}
      {session.state === 'running' && <div className="row board-deliberating" role="status"><Skeleton h={8} w={32} /><span>The board is deliberating</span></div>}
    </div>
  );
}

function CrewTimeline({ decision }: { decision: BoardDecision }) {
  const execution = decision.execution ?? [];
  const steps = scheduledSteps(decision);
  const layout = ganttLayout(steps, decision.crews);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (decision.status !== 'executing') return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [decision.status, decision.review?.at]);
  const planLength = Math.max(0, ...steps.filter((step) => !step.dropped).map((step) => step.endHour));
  const nowHour = planHourNow(decision.review, now, planLength);
  const [ref, width] = useWidth<HTMLDivElement>();
  const tipId = useId();
  const [hover, setHover] = useState<{ taskId: string; left: number } | null>(null);
  const W = Math.max(560, width, layout.maxHour * 36 + 96), left = 72, right = 16, top = 42, laneHeight = 66;
  const plotWidth = W - left - right, axisY = top + layout.laneCount * laneHeight, H = axisY + 42;
  const x = (hour: number) => left + hour / layout.maxHour * plotWidth;
  const active = layout.bars.find((bar) => bar.step.taskId === hover?.taskId)?.step;
  const showTip = (taskId: string, el: SVGGElement) => {
    const box = el.getBoundingClientRect(), chart = ref.current?.getBoundingClientRect();
    setHover({ taskId, left: Math.max(4, Math.min(box.left - (chart?.left ?? 0) + box.width / 2, width - 284)) });
  };
  return (
    <div>
      <div className="card-title">Crew timeline</div>
      <div className="muted" style={{ fontSize: 12, margin: '2px 0 8px' }}>Planned hours. Hover or focus a job for its proposal figures.</div>
      <div className="chart board-timeline" ref={ref} onPointerLeave={() => setHover(null)}>
        <div className="board-gantt-scroll" role="region" aria-label="Scrollable crew timeline" tabIndex={0}>
          <svg width={W} height={H} role="group" aria-label="Jobs by crew, in hours from now">
            {layout.ticks.map((tick) => <g key={tick}>
              <line x1={x(tick)} x2={x(tick)} y1={top - 8} y2={axisY} stroke="var(--grid)" />
              <line x1={x(tick)} x2={x(tick)} y1={axisY} y2={axisY + 4} stroke="var(--axis)" />
              <text x={x(tick)} y={axisY + 18} textAnchor="middle" fontSize="11" fill="var(--text-3)">{fmt(tick)}</text>
            </g>)}
            <line x1={left} x2={W - right} y1={axisY} y2={axisY} stroke="var(--axis)" />
            <text x={left + plotWidth / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="var(--text-3)">Planned hours</text>
            {Array.from({ length: layout.laneCount }, (_, i) => <g key={i}>
              <text x={left - 10} y={top + i * laneHeight + 29} textAnchor="end" fontSize="12" fill="var(--text-2)">Crew {crewName(i + 1)}</text>
              <line x1={left} x2={W - right} y1={top + i * laneHeight + 44} y2={top + i * laneHeight + 44} stroke="var(--grid)" />
            </g>)}
            {layout.responseHour !== null && <g>
              <line x1={x(layout.responseHour)} x2={x(layout.responseHour)} y1={23} y2={axisY} stroke="var(--text-3)" strokeDasharray="3 3" />
              <text x={Math.min(W - right - 36, Math.max(left + 36, x(layout.responseHour)))} y={14} textAnchor="middle" fontSize="11" fill="var(--text-2)">crews arrive</text>
            </g>}
            {layout.bars.map(({ step, lane, start, width: fraction }) => {
              const bx = left + start * plotWidth, by = top + lane * laneHeight + 10, bw = fraction * plotWidth;
              const state = execution.find((item) => item.taskId === step.taskId)?.state;
              const labelWidth = step.assetName.length * 6.6 + 12, inside = bw >= labelWidth + 8;
              const labelX = inside ? bx + bw / 2 : Math.min(W - right - labelWidth / 2, Math.max(left + labelWidth / 2, bx + bw / 2));
              return <g key={step.taskId} className={`board-gantt-bar ${state ? `execution-${state}` : ''}`} tabIndex={0} role="img"
                aria-label={`${step.assetName} on crew ${crewName(step.crewSlot)} from hour ${fmt(step.startHour, 1)} to hour ${fmt(step.endHour, 1)}${state ? `, ${state}` : ''}`}
                aria-describedby={hover?.taskId === step.taskId ? tipId : undefined}
                onPointerEnter={(e) => showTip(step.taskId, e.currentTarget)} onFocus={(e) => showTip(step.taskId, e.currentTarget)}
                onBlur={() => setHover(null)} onKeyDown={(e) => { if (e.key === 'Escape') setHover(null); }}>
                <title>{`${step.assetName}: ${step.action}. ${fmt(step.startHour, 1)}–${fmt(step.endHour, 1)} h; ${fmt(step.hourlyCost)} EGP/h; loss until fixed ${fmt(step.lossUntilFixed)} EGP; repair ${fmt(step.repairCost)} EGP (×${fmt(step.repairMultiplier, 3)}).`}</title>
                <rect x={bx} y={by} width={Math.max(0, bw)} height={30} rx={4} fill={NET[step.agent].color} fillOpacity={0.85} stroke="var(--surface)" strokeWidth={2} />
                <line x1={bx} x2={bx} y1={by + 32} y2={by + 36} stroke="var(--axis)" />
                <line x1={bx + bw} x2={bx + bw} y1={by + 32} y2={by + 36} stroke="var(--axis)" />
                {inside && <rect x={labelX - labelWidth / 2} y={by + 6} width={labelWidth} height={18} rx={3} fill="var(--surface)" />}
                <text x={labelX} y={inside ? by + 19 : by - 5} textAnchor="middle" fontSize="11" fill="var(--text)">{state === 'done' ? '✓ ' : ''}{step.assetName}</text>
              </g>;
            })}
            {decision.status === 'executing' && nowHour !== null && <g className="board-now" aria-label={`Now: planned hour ${fmt(nowHour, 1)}`}>
              <line x1={x(nowHour)} x2={x(nowHour)} y1={top - 12} y2={axisY} stroke="var(--accent)" strokeWidth={2} />
              <text x={Math.min(W - right - 16, Math.max(left + 16, x(nowHour)))} y={top - 16} textAnchor="middle" fontSize="11" fill="var(--text)">now</text>
            </g>}
          </svg>
        </div>
        {active && hover && <div id={tipId} role="tooltip" className="tooltip board-gantt-tip" style={{ left: hover.left, top: 26 }}>
          {decision.review?.override && <div className="muted">Costs below are the board's proposal figures.</div>}
          <b>{active.assetName}</b><div style={{ margin: '2px 0 6px' }}>{active.action}</div>
          <div className="num">Crew {crewName(active.crewSlot)} · {fmt(active.startHour, 1)}–{fmt(active.endHour, 1)} h from now</div>
          <div>Hourly cost: <span className="num">{fmt(active.hourlyCost)} EGP/h</span></div>
          <div>Loss until fixed: <span className="num">{fmt(active.lossUntilFixed)} EGP</span></div>
          <div>Repair cost: <span className="num">{fmt(active.repairCost)} EGP (×{fmt(active.repairMultiplier, 3)})</span></div>
        </div>}
      </div>
      <div className="legend">{Object.entries(NET).filter(([n]) => layout.bars.some(({ step }) => step.agent === n)).map(([n, net]) => <span key={n}><i className="swatch box" style={{ background: net.color }} />{net.label}</span>)}</div>
      {decision.review?.secondsPerHour != null && <p className="sub" style={{ fontSize: 12 }}>On the demo clock, 1 planned hour plays as {fmt(decision.review.secondsPerHour)} seconds. Planned hours and costs stay the same.</p>}
      {execution.length > 0 && <ul className="board-execution">{execution.map((step) => <li key={step.taskId} className={`execution-${step.state}`}>
        <div className="row wrap spread" style={{ gap: 6 }}><b>{step.assetName}</b><span className={`chip ${step.state === 'done' ? 'ok' : step.state === 'working' ? 'warn' : ''}`}>{step.state === 'done' && <Check size={13} aria-hidden />}{step.state.charAt(0).toUpperCase() + step.state.slice(1)}</span></div>
        <div>{step.crewSlot > 0 ? `Crew ${crewName(step.crewSlot)}` : 'No crew assigned'} · <span className="num">{fmt(step.planStartHour, 1)}–{fmt(step.planEndHour, 1)} h</span> planned</div>
        <div className="sub">{step.action}</div>
        {(step.startedAt || step.finishedAt) && <div className="muted">{step.startedAt && `Started ${cairoDateTime(step.startedAt)} Cairo`}{step.finishedAt && ` · Finished ${cairoDateTime(step.finishedAt)} Cairo`}</div>}
        {step.note && <p style={{ margin: 0 }}>{step.note}</p>}
        {step.confirmedAt ? <div className="row wrap" style={{ gap: 4, color: 'var(--ok-text)' }}><Check size={14} aria-hidden />Confirmed by the detector · {cairoDateTime(step.confirmedAt)} Cairo</div> : step.state === 'done' && <div className="muted">Waiting for the detector to confirm</div>}
      </li>)}</ul>}
      {layout.dropped.map((step) => <p key={step.taskId} className="sub" style={{ fontSize: 12, margin: '8px 0 0' }}>No crew needed: {step.assetName} stops costing {step.reliefHour != null ? `at hour ${fmt(step.reliefHour, 1)}` : 'when the other repair finishes'} because another repair removes the cause.</p>)}
    </div>
  );
}

function BoardResult({ decision, onLatest }: { decision: BoardDecision; onLatest: () => void }) {
  const set = useUi((s) => s.set);
  const plan = decision.plan;
  const order = [...plan.steps].sort((a, b) => a.order - b.order).map((step) => step.taskId);
  const chosen = decision.alternatives.find((alt) => alt.order.length === order.length && alt.order.every((id, i) => id === order[i]));
  const alternatives = [...decision.alternatives, ...(!chosen ? [{ id: 'chosen', name: 'Chosen order', order, expectedCost: plan.expectedCost, extraCost: 0 }] : [])].sort((a, b) => a.expectedCost - b.expectedCost);
  const max = Math.max(1, ...alternatives.map((alt) => alt.expectedCost));
  const tile = (label: string, value: number, unit: string, foot?: string, good = false) => <div className="card kpi" key={label}>
    <div className="kpi-label">{label}</div>
    <div className="kpi-value num" title={`${fmt(value, 2)} ${unit}`} style={good ? { color: 'var(--ok-text)' } : undefined}><b>{unit === '%' ? fmt(value) : compact(value)}</b><span className="kpi-unit">{unit}</span></div>
    {foot && <div className="kpi-foot">{foot}</div>}
  </div>;
  return (
    <section className="board-decision stack" aria-label="Board decision" style={{ gap: 18 }}>
      <div><h3 className="card-title" style={{ margin: '0 0 6px' }}>What to do</h3><p style={{ margin: '0 0 6px' }}>{decision.summary}</p><SourceTag source={decision.summarySource} /></div>
      <DecisionControl decision={decision} onLatest={onLatest} />
      <CrewTimeline decision={decision} />
      <div className="board-kpis">
        {tile('Expected cost', plan.expectedCost, 'EGP')}
        {tile('Cheapest in', plan.winShare * 100, '%', `of ${fmt(plan.runs)} price scenarios`)}
        {tile('Bad case', plan.p90, 'EGP', 'one price scenario in ten costs this much or more')}
        {tile('Open problems cost', plan.hourlyBleed, 'EGP per hour')}
        {decision.extraCrewSaving != null && tile('One more crew saves', decision.extraCrewSaving, 'EGP', undefined, true)}
      </div>
      <div className="stack" style={{ gap: 10 }}>
        <h3 className="card-title" style={{ margin: 0 }}>Compared with other orders</h3>
        {alternatives.map((alt) => {
          const isChosen = alt.id === (chosen?.id ?? 'chosen');
          return <div key={alt.id}>
            <div className="row wrap spread" style={{ gap: '3px 10px', marginBottom: 4 }}><span style={{ fontWeight: isChosen ? 600 : 400 }}>{alt.name}{isChosen && <span className="tag" style={{ marginInlineStart: 6 }}>This plan</span>}</span><span className="num">{fmt(alt.expectedCost)} EGP</span></div>
            <Meter value={alt.expectedCost / max} label={`${alt.name}, ${fmt(alt.expectedCost)} EGP`} />
            <div className="num" style={{ fontSize: 12, marginTop: 3 }}>{alt.extraCost === 0 ? (isChosen ? 'This plan' : 'same') : `${alt.extraCost > 0 ? '+' : '−'}${fmt(Math.abs(alt.extraCost))} EGP`}</div>
          </div>;
        })}
      </div>
      {decision.flips.length > 0 && <div><h3 className="card-title" style={{ margin: '0 0 6px' }}>What would change the order</h3><ul className="board-list">{decision.flips.map((flip) => <li key={flip.taskId}>If {flip.assetName} cost per hour were {flip.direction} <span className="num">{fmt(flip.threshold)} EGP</span> (now <span className="num">{fmt(flip.current)} EGP</span>).</li>)}</ul></div>}
      {decision.risks.length > 0 && <div><h3 className="card-title" style={{ margin: '0 0 6px' }}>Risks</h3><ul className="board-risks">{decision.risks.map((risk, i) => {
        const Icon = risk.kind === 'overload' ? Zap : risk.kind === 'queue' ? Clock3 : TriangleAlert;
        return <li key={i}><Icon size={15} className="muted" aria-hidden /><span>{risk.text}</span></li>;
      })}</ul></div>}
      {decision.calculationId && <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={() => set({ calcId: decision.calculationId })}>How this was calculated</button>}
      {!decision.conflict && <div className="muted" style={{ fontSize: 12 }}>There are enough crews to start every job at once.</div>}
    </section>
  );
}

export function BoardPanel({ incidentId }: { incidentId: string }) {
  const session = useStore(latestBoardFor(incidentId));
  const running = useStore((s) => Object.values(s.boards).some((board) => board.state === 'running'));
  const snapshotReady = useStore((s) => s.snapshotReady);
  const notify = useUi((s) => s.notify);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (latestBoardFor(incidentId)(useStore.getState())) { setLoading(false); setError(null); return; }
    const controller = new AbortController();
    // A reset invalidates a REST reply that was already in flight.
    const unsubscribe = useStore.subscribe((next, previous) => {
      if (next.boards !== previous.boards && Object.keys(next.boards).length === 0) { controller.abort(); setLoading(false); }
    });
    setLoading(true); setError(null);
    request<BoardSession>(`/board/latest?incidentId=${encodeURIComponent(incidentId)}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) })
      .then((data) => { if (!controller.signal.aborted) useStore.getState().loadBoardSession(data); })
      .catch((e: unknown) => {
        if (controller.signal.aborted || (e instanceof RequestError && ['no_session', '404'].includes(e.code))) return;
        setError(e instanceof Error ? e.message : 'The board session could not be loaded.');
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { unsubscribe(); controller.abort(); };
  }, [incidentId, snapshotReady, retry]);

  const convene = async () => {
    setBusy(true);
    let reset = false;
    const unsubscribe = useStore.subscribe((next, previous) => {
      if (next.boards !== previous.boards && Object.keys(next.boards).length === 0) reset = true;
    });
    try {
      const data = await post<BoardSession>('/board/convene', { incidentId });
      if (!reset) useStore.getState().loadBoardSession(data);
    }
    catch (e) { notify(e instanceof Error ? e.message : 'The board could not be convened.'); }
    finally { unsubscribe(); setBusy(false); }
  };
  return (
    <Card className="board-panel" title="Board of agents" sub="Water, electricity and cooling agents agree who gets a crew first"
      right={session ? <StatusChip s={session.state === 'running' ? 'warning' : 'normal'} label={session.state === 'running' ? 'In session' : 'Decided'} /> : <span className="chip">Not met</span>}>
      <div className="row wrap spread" style={{ gap: 8, marginBottom: 12 }}>
        <button type="button" className="btn" disabled={busy || running} onClick={convene}><Scale size={16} aria-hidden />{busy ? 'Convening the board…' : 'Convene the board'}</button>
        {loading && !session && <span className="muted" style={{ fontSize: 12 }}>Checking for a meeting…</span>}
      </div>
      {!session && loading && <Skeleton h={48} />}
      {!session && !loading && !error && <p className="muted" style={{ margin: 0, fontSize: 13 }}>Convene the board to agree the crew order for the open problems.</p>}
      {!session && error && <div className="row wrap" style={{ gap: 8 }}><span className="sub">{error}</span><button type="button" className="btn sm" onClick={() => setRetry((n) => n + 1)}>Retry</button></div>}
      {session && <Transcript session={session} />}
      {session?.decision && <div id={`decision-${session.id}`}><BoardResult key={session.id} decision={session.decision} onLatest={() => {
        const revision = useStore.getState().auditRevision;
        void request<BoardSession>(`/board/latest?incidentId=${encodeURIComponent(incidentId)}`).then((latest) => {
          if (useStore.getState().auditRevision !== revision) return;
          useStore.getState().loadBoardSession(latest);
          requestAnimationFrame(() => document.getElementById(`decision-${latest.id}`)?.scrollIntoView({ block: 'start' }));
        }).catch((e: unknown) => notify(e instanceof Error ? e.message : 'Could not load the latest proposal.'));
      }} /></div>}
    </Card>
  );
}
