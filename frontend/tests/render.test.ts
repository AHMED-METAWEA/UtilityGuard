import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { FindingCard } from '../src/components/FindingCard';
import { useStore } from '../src/store';
import type { AgentFinding, Alert } from '../src/api/types';

const fixture = async (path: string) => JSON.parse(await readFile(new URL(`../../fixtures/${path}`, import.meta.url), 'utf8'));

test('finding renders authoritative totals, evidence, key formulas and review actions from regenerated fixtures', async () => {
  const en = JSON.parse(await readFile(new URL('../src/i18n/en.json', import.meta.url), 'utf8'));
  await i18n.use(initReactI18next).init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
  const finding: AgentFinding = (await fixture('ws/agent_finding.json')).payload;
  const alerts: Alert[] = await fixture('rest/alerts.json');
  // React's server snapshot reads Zustand's initial state. Hydrate that baseline for SSR.
  Object.assign(useStore.getInitialState(), { alerts: { data: alerts, status: 'ready' }, incidents: [] });
  useStore.setState({ alerts: { data: alerts, status: 'ready' }, incidents: [] });
  // Deliberately inconsistent totals ensure the component renders backend fields and never recomputes.
  const updated: AgentFinding = { ...finding, fallback: true, numberCheck: 'corrected', cost: { ...finding.cost, costOfDelay: 99999, expectedSaving: 12345 } };
  const html = renderToStaticMarkup(createElement(FindingCard, { finding: updated, onAssumptions() {}, onCalculation() {} }));
  for (const text of ['critical', '92%', 'Why this decision', 'measured', 'rule', 'Water loss per hour', 'Cost of delay per hour', 'Saving from acting now', '140 m3/h × 8 EGP/m3', '99,999', '12,345', '34,676–37,364', 'Recorded answer', 'Figures corrected', 'Download report', 'Open calculation', 'Edit assumptions']) assert.ok(html.toLowerCase().includes(text.toLowerCase()), text);
  assert.ok(!html.includes('<strong>Repair now</strong>'), 'non-key calculation stays behind disclosure');
  assert.ok(!html.includes('54,420'), 'delay is not derived locally');
});
