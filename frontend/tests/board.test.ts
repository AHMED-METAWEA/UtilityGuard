import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dispatchMessage, parseEnvelope } from '../src/api/ws';
import type { BoardSession, BoardState, BoardStep, SnapshotPayload } from '../src/api/types';
import { ganttLayout, ganttPosition } from '../src/charts/gantt';
import { latestBoardFor, useStore } from '../src/store';

const fixture = async (path: string) => JSON.parse(await readFile(new URL(`../../fixtures/${path}.json`, import.meta.url), 'utf8'));
const session = await fixture('rest/board_session') as BoardSession;
const state = await fixture('ws/board_state') as { type: 'board_state'; payload: BoardState };
const decision = session.decision!;

test('all board WS fixtures parse; additive fields are accepted', async () => {
  for (const name of ['board_state', 'board_message', 'board_decision']) {
    const envelope = await fixture(`ws/${name}`);
    assert.equal(parseEnvelope(JSON.stringify(envelope))?.type, name);
    assert.equal(parseEnvelope(JSON.stringify({ ...envelope, future: true, payload: { ...envelope.payload, future: true } }))?.type, name);
  }
  assert.equal(parseEnvelope(JSON.stringify({ type: 'future_board', payload: {} })), null);
});

test('malformed board fields and nested lists are rejected', async () => {
  for (const [name, patch] of [
    ['board_state', { state: 'future' }],
    ['board_state', { sessionId: 3 }],
    ['board_message', { text: 3 }],
    ['board_message', { round: -1 }],
    ['board_message', { facts: [{ label: 'Invalid', value: 'three', unit: 'h', source: 'measured' }] }],
    ['board_decision', { incidentIds: [3] }],
    ['board_decision', { plan: { ...decision.plan, steps: [{ ...decision.plan.steps[0], endHour: 'later' }] } }],
    ['board_decision', { risks: [{ kind: 'unknown', text: 'Invalid' }] }],
  ] as const) {
    const envelope = await fixture(`ws/${name}`);
    assert.equal(parseEnvelope(JSON.stringify({ ...envelope, payload: { ...envelope.payload, ...patch } })), null, `${name}: ${JSON.stringify(patch)}`);
  }
});

test('board events retain transcript order, deduplicate messages and select shared incidents', async () => {
  useStore.getState().clearBoards();
  dispatchMessage(state);
  for (const message of session.messages) dispatchMessage({ type: 'board_message', payload: message });
  dispatchMessage({ type: 'board_message', payload: session.messages[0] });
  const envelope = parseEnvelope(JSON.stringify(await fixture('ws/board_decision')));
  assert.ok(envelope);
  dispatchMessage(envelope);
  const board = useStore.getState().boards[session.id];
  assert.deepEqual(board.messages, session.messages);
  assert.deepEqual(board.messages.map((m) => m.id), session.messages.map((m) => m.id));
  assert.deepEqual(board.decision, decision);
  assert.equal(board.state, 'done');
  assert.equal(latestBoardFor(session.incidentId!)(useStore.getState()), board);
  for (const id of decision.incidentIds) assert.equal(latestBoardFor(id)(useStore.getState()), board);
  assert.equal(latestBoardFor('no-such-incident')(useStore.getState()), undefined);
});

test('REST hydration merges live messages without losing a streamed decision or regressing state', () => {
  const store = useStore.getState();
  store.clearBoards();
  store.appendBoardMessage(session.messages[1]);
  store.appendBoardMessage(session.messages[2]);
  store.setBoardDecision(decision);
  store.loadBoardSession({ ...session, state: 'running', messages: session.messages.slice(0, 2), decision: undefined });
  let board = useStore.getState().boards[session.id];
  assert.deepEqual(board.messages, session.messages.slice(0, 3));
  assert.equal(board.decision, decision);
  assert.equal(board.state, 'done');
  store.applyBoardState({ ...state.payload, state: 'running' });
  assert.equal(useStore.getState().boards[session.id].state, 'done');
  store.loadBoardSession(session);
  store.loadBoardSession(session);
  board = useStore.getState().boards[session.id];
  assert.deepEqual(board.messages, session.messages);
  assert.equal(board.startedAt, session.startedAt);
});

test('the newest startedAt wins across direct and decision incident membership', () => {
  const store = useStore.getState();
  store.clearBoards();
  store.loadBoardSession({ ...session, id: 'older', startedAt: '2026-10-27T12:00:00Z' });
  store.loadBoardSession({ ...session, id: 'newer', incidentId: undefined, startedAt: '2026-10-29T12:00:00Z' });
  assert.equal(latestBoardFor(session.incidentId!)(useStore.getState())?.id, 'newer');
  store.loadBoardSession({ ...session, id: 'direct', startedAt: '2026-10-30T12:00:00Z', decision: undefined });
  assert.equal(latestBoardFor(session.incidentId!)(useStore.getState())?.id, 'direct');
});

test('a late state event adds metadata after a message, and dated sessions win over pending metadata', () => {
  const store = useStore.getState();
  store.clearBoards();
  store.appendBoardMessage(session.messages[0]);
  store.setBoardDecision(decision);
  store.loadBoardSession({ ...session, id: 'dated', startedAt: '2026-10-29T12:00:00Z' });
  assert.equal(latestBoardFor(session.incidentId!)(useStore.getState())?.id, 'dated');
  store.applyBoardState(state.payload);
  assert.equal(useStore.getState().boards[session.id].startedAt, state.payload.startedAt);
  assert.equal(useStore.getState().boards[session.id].messages[0], session.messages[0]);
});

test('applySnapshot clears all boards, as does clearBoards', async () => {
  const store = useStore.getState();
  store.loadBoardSession(session);
  assert.ok(Object.keys(useStore.getState().boards).length > 0);
  store.applySnapshot((await fixture('ws/snapshot')).payload as SnapshotPayload);
  assert.deepEqual(useStore.getState().boards, {});
  assert.equal(latestBoardFor(session.incidentId!)(useStore.getState()), undefined);
  store.loadBoardSession(session);
  store.clearBoards();
  assert.deepEqual(useStore.getState().boards, {});
});

test('Gantt positions use hour fractions and one lane per available crew', () => {
  const position = ganttPosition(1, 2.5, 6);
  assert.equal(position.start, 1 / 6);
  assert.equal(position.end, 2.5 / 6);
  assert.equal(position.width, 1.5 / 6);
  const layout = ganttLayout(decision.plan.steps, decision.crews);
  assert.equal(layout.maxHour, 6);
  assert.equal(layout.laneCount, 2);
  assert.deepEqual(layout.ticks, [0, 1, 2, 3, 4, 5, 6]);
  assert.equal(layout.responseHour, 1);
  assert.deepEqual(layout.bars.map((b) => b.lane), [0, 1, 0]);
  assert.deepEqual({ start: layout.bars[0].start, end: layout.bars[0].end, width: layout.bars[0].width }, position);
  assert.equal(layout.bars[2].start, 0.5);
  assert.equal(layout.bars[2].end, 1);
});

test('Gantt uses two-hour ticks above 12h, excludes dropped problems and retains idle lanes', () => {
  const dropped: BoardStep = { ...decision.plan.steps[0], taskId: 'dropped', crewSlot: 0, dropped: true, startHour: 0, endHour: 99, reliefHour: 2.5 };
  const layout = ganttLayout([{ ...decision.plan.steps[0], endHour: 15.5 }, dropped], 3);
  assert.equal(layout.maxHour, 15.5);
  assert.equal(layout.laneCount, 3);
  assert.deepEqual(layout.ticks, [0, 2, 4, 6, 8, 10, 12, 14]);
  assert.equal(layout.bars.length, 1);
  assert.deepEqual(layout.dropped, [dropped]);
  const empty = ganttLayout([dropped], 2);
  assert.equal(empty.responseHour, null);
  assert.equal(empty.laneCount, 2);
  assert.equal(empty.maxHour, 1);
  assert.deepEqual(ganttPosition(0, 0, 0), { start: 0, end: 0, width: 0 });
  assert.deepEqual(ganttLayout([{ ...decision.plan.steps[0], endHour: 12 }], 1).ticks, Array.from({ length: 13 }, (_, i) => i));
  assert.equal(ganttLayout([{ ...decision.plan.steps[0], startHour: 0.1, endHour: 0.5 }], 1).maxHour, 0.5);
});
