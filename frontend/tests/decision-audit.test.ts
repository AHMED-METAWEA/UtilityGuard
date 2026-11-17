import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { AuditEntry, BoardDecision, SnapshotPayload } from '../src/api/types';
import { parseEnvelope, dispatchMessage } from '../src/api/ws';
import { useStore } from '../src/store';
import { auditEntries } from '../src/store/slices';
import { canApplyReview, scheduledSteps, planHourNow, moveOrder, proposalOrder, reviewError } from '../src/lib/decision';
import { actorRole, auditChips, auditGroup, auditLabel, cairoDateTime } from '../src/lib/audit';
import { href, parse } from '../src/lib/route';
import { RequestError } from '../src/api/client';

const fixture = async (path: string) => JSON.parse(await readFile(new URL(`../../fixtures/${path}.json`, import.meta.url), 'utf8'));
const executing = await fixture('rest/board_decision_executing') as BoardDecision;
const audit = await fixture('rest/audit') as { entries: AuditEntry[] };
const oldDecision = () => {
  const { status: _status, review: _review, execution: _execution, ...old } = executing;
  void _status; void _review; void _execution;
  return old;
};
const envelope = (payload: unknown) => parseEnvelope(JSON.stringify({ type: 'board_decision', payload }));

test('demo plan hour uses seconds per hour, clamps both ends, and rejects missing or invalid clock inputs', () => {
  const review = { at: '2026-10-28T11:30:00Z', secondsPerHour: 20 };
  const start = Date.parse(review.at);
  assert.equal(planHourNow(review, start + 30000, 6), 1.5);
  assert.equal(planHourNow(review, start - 10000, 6), 0);
  assert.equal(planHourNow(review, start + 999000, 6), 6);
  assert.equal(planHourNow(review, start + 30000, 0), 0);
  assert.equal(planHourNow(undefined, start, 6), null);
  for (const secondsPerHour of [null, undefined, 0, -1, Infinity, NaN]) assert.equal(planHourNow({ ...review, secondsPerHour }, start, 6), null);
  assert.equal(planHourNow({ ...review, at: 'invalid' }, start, 6), null);
  assert.equal(planHourNow(review, NaN, 6), null);
});

test('reordering is immutable, keeps every task exactly once, and stops at boundaries', () => {
  const order = ['a', 'b', 'c'];
  assert.deepEqual(moveOrder(order, 1, -1), ['b', 'a', 'c']);
  assert.deepEqual(moveOrder(order, 1, 1), ['a', 'c', 'b']);
  assert.deepEqual(order, ['a', 'b', 'c']);
  for (const [index, direction] of [[0, -1], [2, 1], [-1, 1], [3, -1]] as const) assert.equal(moveOrder(order, index, direction), order);
  const proposal = proposalOrder(executing);
  assert.deepEqual(proposal, [...executing.plan.steps].sort((a, b) => a.order - b.order).map((step) => step.taskId));
  assert.equal(new Set(proposal).size, executing.tasks.length);
  const withDropped = { ...executing, tasks: [...executing.tasks, { ...executing.tasks[0], id: 'dropped' }] };
  assert.equal(proposalOrder(withDropped).at(-1), 'dropped');
});

test('legacy decisions default to proposed with no execution, and all six contract statuses parse', () => {
  const parsed = envelope(oldDecision());
  assert.equal(parsed?.type, 'board_decision');
  if (parsed?.type === 'board_decision') {
    assert.equal(parsed.payload.status, 'proposed');
    assert.deepEqual(parsed.payload.execution, []);
  }
  for (const status of ['proposed', 'executing', 'completed', 'rejected', 'superseded', 'stale']) assert.ok(envelope({ ...executing, status }));
  assert.ok(envelope({ ...executing, future: true, review: { ...executing.review, future: true }, execution: executing.execution.map((step) => ({ ...step, future: true })) }));
});

test('malformed review, execution, status, and audit payloads are rejected', async () => {
  for (const patch of [
    { status: 'made-up' }, { status: null }, { execution: null }, { execution: [{}] },
    { review: { ...executing.review, operator: 12 } }, { review: { ...executing.review, costDelta: 'ten' } },
    { review: { ...executing.review, override: 'yes' } }, { review: { ...executing.review, order: [1] } },
    { execution: [{ ...executing.execution[0], state: 'almost' }] },
    { execution: [{ ...executing.execution[0], confirmedAt: 123 }] },
    { execution: [{ ...executing.execution[0], planEndHour: 'later' }] },
  ]) assert.equal(envelope({ ...executing, ...patch }), null, JSON.stringify(patch));
  const valid = await fixture('ws/audit_entry');
  assert.equal(parseEnvelope(JSON.stringify(valid))?.type, 'audit_entry');
  for (const patch of [{ seq: -1 }, { seq: 1.5 }, { seq: '1' }, { data: { nested: {} } }, { prevHash: 2 }, { sessionId: false }]) {
    assert.equal(parseEnvelope(JSON.stringify({ ...valid, payload: { ...valid.payload, ...patch } })), null);
  }
});

test('decision status pushes replace the session decision and keep its transcript', async () => {
  const session = await fixture('rest/board_session');
  const store = useStore.getState(); store.clearBoards(); store.loadBoardSession(session);
  const parsed = envelope(executing); assert.ok(parsed); dispatchMessage(parsed);
  const board = useStore.getState().boards[executing.sessionId];
  assert.deepEqual(board.decision, executing);
  assert.deepEqual(board.messages, session.messages);
  const complete = envelope({ ...executing, status: 'completed', execution: executing.execution.map((step) => ({ ...step, state: 'done' })) });
  assert.ok(complete); dispatchMessage(complete);
  assert.equal(useStore.getState().boards[executing.sessionId].decision?.status, 'completed');
});

test('audit sequence deduplicates, sorts oldest first, caps at 500, and fetched lists replace history', () => {
  const first = audit.entries[0];
  const entries = Array.from({ length: 510 }, (_, i) => ({ ...first, seq: i + 1 }));
  const duplicate = { ...entries[200], detail: 'Updated entry' };
  const normalized = auditEntries([...entries.slice().reverse(), duplicate]);
  assert.equal(normalized.length, 500);
  assert.equal(normalized[0].seq, 11); assert.equal(normalized.at(-1)?.seq, 510);
  assert.equal(normalized.find((entry) => entry.seq === duplicate.seq)?.detail, 'Updated entry');
  const store = useStore.getState(); store.clearAudit();
  store.replaceAudit(entries);
  store.appendAuditEntry({ ...first, seq: 511 });
  store.appendAuditEntry({ ...first, seq: 511 });
  assert.equal(useStore.getState().audit.length, 500);
  assert.equal(useStore.getState().audit[0].seq, 12);
  store.replaceAudit(audit.entries);
  assert.deepEqual(useStore.getState().audit, audit.entries);
});

test('live audit pushes dispatch, and clear/reset invalidates an in-flight fetch generation', async () => {
  const store = useStore.getState(); store.clearAudit();
  const parsed = parseEnvelope(JSON.stringify(await fixture('ws/audit_entry'))); assert.ok(parsed); dispatchMessage(parsed); dispatchMessage(parsed);
  assert.equal(useStore.getState().audit.length, 1);
  const revision = useStore.getState().auditRevision;
  store.clearAudit(); assert.equal(useStore.getState().auditRevision, revision + 1);
  store.replaceAudit(audit.entries);
  store.applySnapshot((await fixture('ws/snapshot')).payload as SnapshotPayload);
  assert.deepEqual(useStore.getState().audit, []);
  assert.equal(useStore.getState().auditRevision, revision + 2);
});

test('all action labels have readable groups, actors and key data chips', () => {
  const actions = {
    Proposals: ['proposed', 'superseded', 'proposal_stale'], Decisions: ['approve', 'approve_with_changes', 'reject'],
    'Crew work': ['crew_dispatched', 'repair_done', 'no_crew_needed', 'skipped', 'plan_completed'], Detector: ['alert_cleared'], System: ['simulation_reset'],
  };
  for (const [group, values] of Object.entries(actions)) for (const action of values) {
    assert.equal(auditGroup(action), group);
    assert.ok(!auditLabel(action).includes('_'));
  }
  assert.equal(auditGroup('future_action'), 'System'); assert.equal(auditLabel('future_action'), 'Future action');
  for (const actor of ['board', 'dispatch', 'detector', 'system']) assert.equal(actorRole(actor), actor);
  assert.equal(actorRole('Eng. Mona'), 'person');
  assert.deepEqual(auditChips({ expected_cost: 120.25, cost_delta: 0, override: false, ignored: 'value' }), [
    { label: 'Expected cost', value: 120.25 }, { label: 'Cost delta', value: 0 }, { label: 'Override', value: false },
  ]);
  assert.match(cairoDateTime('2026-01-01T00:00:00Z'), /02:00:00/);
});

test('audit session routes and review conflict messages are actionable', () => {
  assert.deepEqual(parse(href('audit', 'board-0001')), { page: 'audit', id: 'board-0001' });
  assert.deepEqual(parse('#/audit'), { page: 'audit', id: undefined });
  assert.match(reviewError(new RequestError('already_superseded', 'raw')), /newer proposal/);
  assert.match(reviewError(new RequestError('nothing_to_do', 'raw')), /already cleared/);
  assert.match(reviewError(new RequestError('already_executing', 'raw')), /already been reviewed/);
  assert.equal(reviewError(new RequestError('bad_order', 'Every task is required')), 'Every task is required');
});

test('execution schedule follows an override, including jobs the proposal dropped', () => {
  const changed = { ...executing, plan: { ...executing.plan, steps: executing.plan.steps.map((step, index) => index === 0 ? { ...step, dropped: true, crewSlot: 0 } : step) },
    execution: executing.execution.map((step, index) => index === 0 ? { ...step, crewSlot: 2, planStartHour: 4, planEndHour: 5.5 } : step) };
  const scheduled = scheduledSteps(changed);
  assert.equal(scheduled[0].crewSlot, 2);
  assert.equal(scheduled[0].startHour, 4); assert.equal(scheduled[0].endHour, 5.5); assert.equal(scheduled[0].dropped, false);
  assert.equal(scheduled[0].repairCost, executing.plan.steps[0].repairCost);
  assert.deepEqual(scheduledSteps({ ...executing, execution: [] }), executing.plan.steps);
});

test('review replies cannot resurrect reset sessions or regress newer streamed execution', () => {
  const proposed = { ...executing, status: 'proposed' as const, review: null, execution: [] };
  assert.equal(canApplyReview(proposed, proposed, 1, 1), true);
  assert.equal(canApplyReview(proposed, { ...proposed }, 1, 1), true);
  assert.equal(canApplyReview(proposed, executing, 1, 1), false);
  assert.equal(canApplyReview(proposed, { ...executing, status: 'completed' }, 1, 1), false);
  assert.equal(canApplyReview(proposed, { ...proposed }, 1, 2), false);
  assert.equal(canApplyReview(proposed, undefined, 1, 1), false);
});
