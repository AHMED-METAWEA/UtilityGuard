import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { CityDecision, CityDistricts, CityFacility } from '../src/api/city';
import { embedUrl, healthUrl } from '../src/lib/health';
import { CITY_LAYERS, band, cityBounds, detailLines, drawnAt, facilityLine, GROUPS, groupCounts, hospitalLine, isCityDecision, pressureLine, isCityDistricts, sortDecision, matchNote, monthLabel, radiusFor, scoreOf } from '../src/lib/city';

const fixture = async (name: string) => JSON.parse(await readFile(new URL(`../../fixtures/rest/${name}.json`, import.meta.url), 'utf8'));
const city = await fixture('city_districts') as CityDistricts;
const facilities = await fixture('city_facilities') as CityFacility[];
const decision = await fixture('city_decision') as CityDecision;

test('the real fixture is the shape the map reads: 15 districts, 21 months, a source with a hash', () => {
  assert.ok(isCityDistricts(city));
  assert.equal(city.districts.length, 15);
  assert.equal(city.months.length, 21);
  assert.equal(city.month, city.months[city.months.length - 1]);
  assert.match(city.source.sha256, /^[0-9a-f]{64}$/);
  assert.equal(city.source.simulated, true);
  assert.ok(!isCityDistricts({ ...city, districts: [{ ...city.districts[0], matchKind: 'near' }] }));
  assert.ok(!isCityDistricts(null));
});

test('bands follow the healthcare team\'s stress index: under 20 low, 20 to under 35 moderate, 35 and over high', () => {
  assert.equal(band(0), 'low'); assert.equal(band(19.99), 'low');
  assert.equal(band(20), 'moderate'); assert.equal(band(34.99), 'moderate');
  assert.equal(band(35), 'high'); assert.equal(band(89), 'high');
});

test('every layer reads a 0 to 100 score from every district', () => {
  for (const layer of CITY_LAYERS) for (const d of city.districts) {
    const v = scoreOf(d, layer.id);
    assert.ok(Number.isFinite(v) && v >= 0 && v <= 100, `${layer.id} ${d.districtKey} ${v}`);
  }
});

test('circle size grows with residents, stays readable for a tiny district and survives zero', () => {
  const max = Math.max(...city.districts.map((d) => d.metrics.residents));
  const sizes = city.districts.map((d) => radiusFor(d.metrics.residents, max));
  assert.equal(radiusFor(max, max), 22);
  assert.ok(Math.min(...sizes) >= 7 && Math.max(...sizes) <= 22);
  assert.ok(radiusFor(40000, 40000) > radiusFor(10000, 40000));
  assert.equal(radiusFor(0, 0), 8);
  assert.equal(radiusFor(-5, 100), 7);
});

test('months read as words and a bad month is left alone rather than shown wrong', () => {
  assert.equal(monthLabel('2026-09'), 'Sep 2026');
  assert.equal(monthLabel('2025-01'), 'Jan 2025');
  assert.equal(monthLabel('nonsense'), 'nonsense');
  assert.equal(monthLabel('2026-13'), '2026-13');
});

test('tooltips show real figures with units and never NaN or undefined', () => {
  for (const d of city.districts) for (const layer of CITY_LAYERS) {
    const lines = detailLines(d, layer.id);
    assert.ok(lines.length >= 3);
    for (const l of lines) assert.ok(!/NaN|undefined|null/.test(l.value), `${d.districtKey} ${layer.id} ${l.label} ${l.value}`);
  }
  const cbd = city.districts.find((d) => d.districtKey === '1009')!;
  assert.match(detailLines(cbd, 'air')[1].value, /µg\/m³$/);
  assert.equal(matchNote(cbd), 'Matched to zone-cbd by name and kind; drawn on that zone, position approximate');
  assert.match(matchNote(city.districts.find((d) => d.districtKey === '1005')!), /^No zone of the twin has this name; read as the same kind of district \(zone-res-2, /);
  assert.equal(matchNote(city.districts.find((d) => d.districtKey === '1015')!), 'No counterpart in the twin');
  assert.match(facilityLine(city.districts.find((d) => d.districtKey === '1013')!), /^8 hospitals, \d+ clinics, \d+ specialists$/);
  assert.equal(facilityLine(city.districts.find((d) => d.districtKey === '1011')!), '1 hospital, 1 clinic, 0 specialists');
});

test('the facility counts on the districts add up to the facilities the map draws', () => {
  assert.equal(facilities.length, 81);
  const total = city.districts.reduce((s, d) => s + d.facilities.hospitals + d.facilities.clinics + d.facilities.doctors, 0);
  assert.equal(total, facilities.length);
  for (const d of city.districts) assert.equal(facilities.filter((f) => f.districtKey === d.districtKey && f.kind === 'hospital').length, d.facilities.hospitals);
});

test('the bounds hold every district and facility', () => {
  const b = cityBounds(city.districts, facilities)!;
  for (const p of [...city.districts.map(drawnAt), ...facilities.map(drawnAt)].filter((p) => p !== null)) {
    assert.ok(p.lng >= b[0] && p.lng <= b[2] && p.lat >= b[1] && p.lat <= b[3]);
  }
  assert.equal(cityBounds([], []), null);
});

test('a district is drawn on the zone it is matched to, and not at all when it has no match', () => {
  const gov = city.districts.find((d) => d.districtKey === '1010')!, park = city.districts.find((d) => d.districtKey === '1015')!;
  assert.ok(gov.placedAt && drawnAt(gov) === gov.placedAt && drawnAt(gov) !== gov.position);
  assert.equal(park.placedAt, undefined);
  assert.equal(drawnAt(park), null); // no counterpart: not drawn
  assert.ok(city.districts.filter((d) => d.matchKind !== 'none').every((d) => d.placedAt));
  assert.ok(facilities.filter((f) => city.districts.find((d) => d.districtKey === f.districtKey)?.placedAt).every((f) => f.placedAt));
});

test('the joint table has a row per district and every group is one of the four plus no counterpart', () => {
  assert.ok(isCityDecision(decision));
  assert.equal(decision.rows.length, 15);
  assert.equal(Object.values(groupCounts(decision)).reduce((a, b) => a + b, 0), 15);
  assert.equal(decision.rows.find((r) => r.districtKey === '1015')!.group, 'no_outage_data');
  assert.equal(decision.rows.find((r) => r.districtKey === '1015')!.worstCostOfDelayEgp, undefined);
});

test('the groups follow the medians and are listed most pressing first, costlier outage first inside a group', () => {
  for (const r of decision.rows.filter((x) => x.worstCostOfDelayEgp !== undefined)) {
    assert.equal(r.worstCostOfDelayEgp! > decision.medianCostOfDelayEgp, r.outageVsMedian === 'higher');
    assert.equal(r.esiOverall > decision.medianEsiOverall, r.environmentVsMedian === 'higher');
  }
  const sorted = sortDecision(decision.rows);
  const order = GROUPS.map((g) => g.id);
  for (let i = 1; i < sorted.length; i++) {
    const a = sorted[i - 1], b = sorted[i];
    assert.ok(order.indexOf(a.group) <= order.indexOf(b.group));
    if (a.group === b.group) assert.ok((a.worstCostOfDelayEgp ?? 0) >= (b.worstCostOfDelayEgp ?? 0));
  }
  assert.equal(sorted.length, decision.rows.length);
});

test('the hospital column says how sure the reach is', () => {
  const row = (over: object) => ({ ...decision.rows[0], ...over });
  assert.equal(hospitalLine(row({ hospitals: 0 })), 'No hospital');
  assert.equal(hospitalLine(row({ hospitals: 2, hospitalScenariosSure: 0, hospitalScenariosPossible: 0 })), 'Not reached by any failure');
  assert.equal(hospitalLine(row({ hospitals: 2, hospitalScenariosSure: 1, hospitalScenariosPossible: 0 })), '1 failure reaches them');
  assert.equal(hospitalLine(row({ hospitals: 2, hospitalScenariosSure: 4, hospitalScenariosPossible: 11 })), '4 failures reach them, 11 may reach them');
});

test('the healthcare dashboard is found on port 8501 of the host the twin is opened from, unless told otherwise', () => {
  assert.equal(healthUrl({ protocol: 'http:', hostname: 'localhost' }), 'http://localhost:8501');
  assert.equal(healthUrl({ protocol: 'http:', hostname: '192.168.1.20' }), 'http://192.168.1.20:8501');
  assert.equal(healthUrl({ protocol: 'https:', hostname: 'twin.example' }), 'https://twin.example:8501');
  assert.equal(healthUrl({ protocol: 'http:', hostname: 'localhost' }, ' https://health.example/ '), 'https://health.example');
  assert.equal(healthUrl({ protocol: 'http:', hostname: '' }), 'http://localhost:8501');
  assert.equal(embedUrl('http://localhost:8501'), 'http://localhost:8501/?embed=true');
});

test('their hospital pressure reads as a ratio with the best package, and is absent when their engine figures are not there', () => {
  const row = (over: object) => ({ ...decision.rows[0], ...over });
  assert.deepEqual(pressureLine(row({ pressureBaseline: 1.2271, overCapacity: true, bestPackage: 'A New center + prevention', bestPackagePressure: 0.9766, bestPackageCostMEgp: 40.5 })),
    { main: '1.23 over capacity', sub: 'best: A New center + prevention, leaves 0.98 for 40.5 M EGP' });
  assert.deepEqual(pressureLine(row({ pressureBaseline: 0.75, overCapacity: false, bestPackage: undefined })), { main: '0.75', sub: '' });
  assert.equal(pressureLine(row({ pressureBaseline: undefined })), null);
});
