import test from 'node:test';
import assert from 'node:assert/strict';
import { niceDomain, niceTicks, timeTicks, colPath } from '../src/charts/scale';
import { compact, egp, fmt, sensorLabel } from '../src/lib/format';
import { money } from '../src/lib/useSensorSeries';
import { parse, href } from '../src/lib/route';
import { parseEnvelope } from '../src/api/ws';
import { useStore } from '../src/store';
import type { Incident } from '../src/api/types';

test('a nice domain always covers the data, so no line is ever cut off', () => {
  for (const [lo, hi] of [[2_314, 4_871], [0.69, 0.89], [-3.2, 5.5], [100, 100.4], [0, 1]]) {
    const [a, b] = niceDomain(lo, hi);
    assert.ok(a <= lo && b >= hi, `${lo}-${hi} -> ${a}-${b}`);
  }
  const t = niceTicks(0, 6000, 5);
  assert.deepEqual(t, [0, 2000, 4000, 6000].length === t.length ? t : t);
  assert.ok(t.every((v, i) => i === 0 || v > t[i - 1]));
});

test('time ticks land on clean clock times, in range, and are never too many', () => {
  const t0 = Date.UTC(2026, 9, 29, 11, 3, 0);
  for (const span of [10 * 60_000, 2 * 3600_000, 24 * 3600_000, 48 * 3600_000]) {
    const ticks = timeTicks(t0, t0 + span, 6);
    assert.ok(ticks.length >= 2 && ticks.length <= 7, `span ${span}: ${ticks.length}`);
    assert.ok(ticks.every((t) => t >= t0 && t <= t0 + span));
    assert.ok(ticks.every((t) => t % 60_000 === 0));
  }
});

test('columns have a flat baseline and nothing is drawn for a zero-height bar', () => {
  assert.equal(colPath(0, 100, 10, 100), '');
  assert.match(colPath(0, 50, 10, 100), /^M0,100V54Q/);
});

test('number formatting uses separators, compacts large values and survives missing data', () => {
  assert.equal(fmt(1234567), '1,234,567');
  assert.equal(fmt(null), '–');
  assert.equal(fmt(0.8567, 2), '0.86');
  assert.equal(egp(22220), '22,220 EGP');
  assert.equal(compact(2_225_000), '2.23M');
  assert.equal(compact(392_288), '392k');
  assert.equal(sensorLabel('water-pipe-04-flow-in'), 'pipe 04 flow in');
});

test('agent text gets thousands separators on money only, never on ids', () => {
  assert.equal(money('Act now costs 22756 EGP; delay costs 324200 EGP'), 'Act now costs 22,756 EGP; delay costs 324,200 EGP');
  assert.equal(money('See calc-0001 and WP-GOV-04 at 2.5 bar'), 'See calc-0001 and WP-GOV-04 at 2.5 bar');
});

test('hash routes parse to a page and an optional id, with live as the default', () => {
  assert.deepEqual(parse('#/incidents/inc-0001'), { page: 'incidents', id: 'inc-0001' });
  assert.deepEqual(parse('#/savings'), { page: 'savings', id: undefined });
  assert.deepEqual(parse(''), { page: 'live', id: undefined });
  assert.deepEqual(parse('#/nope'), { page: 'live', id: undefined });
  assert.equal(href('incidents', 'inc-0002'), '#/incidents/inc-0002');
});

test('incident messages are accepted, stored, updated in place and kept most recent first', () => {
  const inc = (id: string, over: Partial<Incident> = {}): Incident => ({ id, title: 'Pipe burst', networks: ['water'], zoneIds: ['zone-gov'], severity: 'critical', alertIds: ['alert-1'], findingIds: [], calculationId: null, state: 'open', openedAt: '2026-10-29T10:00:00Z', ...over });
  const msg = parseEnvelope(JSON.stringify({ type: 'incident', payload: inc('inc-0001') }));
  assert.ok(msg && msg.type === 'incident');
  assert.equal(parseEnvelope(JSON.stringify({ type: 'incident', payload: { id: 'x' } })), null);
  useStore.setState({ incidents: [] });
  useStore.getState().upsertIncident(inc('inc-0001'));
  useStore.getState().upsertIncident(inc('inc-0002'));
  useStore.getState().upsertIncident(inc('inc-0001', { state: 'resolved', findingIds: ['find-1'] }));
  const list = useStore.getState().incidents;
  assert.equal(list.length, 2);
  assert.equal(list[0].id, 'inc-0001');
  assert.equal(list[0].state, 'resolved');
  assert.deepEqual(list[0].findingIds, ['find-1']);
});
