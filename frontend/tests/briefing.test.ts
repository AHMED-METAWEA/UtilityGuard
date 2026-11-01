import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { Briefing, BriefingChannel, BriefingDelivery } from '../src/api/types';
import { sendBriefing } from '../src/api/briefing';
import { briefingSeverity, firstDelivery, isBriefing, isBriefingChannel, isBriefingDelivery, orderSections } from '../src/lib/briefing';

const fixture = async (name: string) => JSON.parse(await readFile(new URL(`../../fixtures/rest/${name}.json`, import.meta.url), 'utf8'));
const english = await fixture('briefing') as Briefing;
const arabic = await fixture('briefing_ar') as Briefing;
const channels = await fixture('briefing_channels') as BriefingChannel[];
const deliveries: BriefingDelivery[] = channels.map((channel) => ({ channel: channel.channel, state: channel.configured ? 'sent' : 'skipped', detail: channel.detail, at: english.generatedAt }));

test('real English and Arabic briefings validate, with identical figure keys and values', () => {
  assert.ok(isBriefing(english)); assert.ok(isBriefing(arabic));
  assert.equal(english.lang, 'en'); assert.equal(arabic.lang, 'ar');
  const values = (briefing: Briefing) => briefing.figures.map(({ key, value }) => ({ key, value })).sort((a, b) => a.key.localeCompare(b.key));
  assert.deepEqual(values(english), values(arabic));
  assert.ok(english.figures.length > 0);
});

test('no fixture text contains unresolved placeholders and Arabic content reads in Arabic', () => {
  const check = (value: unknown) => {
    if (typeof value === 'string') assert.ok(!value.includes('{{'), value);
    else if (Array.isArray(value)) value.forEach(check);
    else if (value && typeof value === 'object') Object.values(value).forEach(check);
  };
  [english, arabic, channels].forEach(check);
  assert.match(arabic.headline, /\p{Script=Arabic}/u);
  assert.match(arabic.text, /\p{Script=Arabic}/u);
  assert.ok(arabic.sections.every((section) => /\p{Script=Arabic}/u.test(section.title) && section.items.every((item) => /\p{Script=Arabic}/u.test(item.text))));
});

test('briefing validator rejects missing sections and malformed nested data', () => {
  const missing = { ...english } as Partial<Briefing>;
  delete missing.sections;
  const section = english.sections[0], item = section.items[0], figure = english.figures[0];
  for (const invalid of [null, [], missing, { ...english, generatedAt: 'invalid' }, { ...english, lang: 'fr' },
    { ...english, headline: null }, { ...english, text: false }, { ...english, repeated: -1 }, { ...english, repeated: 0.5 },
    { ...english, sections: null }, { ...english, sections: [{ ...section, id: 'other' }] },
    { ...english, sections: [{ ...section, title: null }] }, { ...english, sections: [{ ...section, items: [{}] }] },
    { ...english, sections: [{ ...section, items: [{ ...item, severity: 'urgent' }] }] },
    { ...english, sections: [{ ...section, items: [{ ...item, fingerprint: null }] }] },
    { ...english, sections: [{ ...section, items: [{ ...item, link: 'javascript:alert(1)' }] }] },
    { ...english, figures: [{ ...figure, value: Infinity }] }, { ...english, figures: [{ ...figure, display: null }] },
    { ...english, deliveries: [{ ...deliveries[0], state: 'queued' }] },
  ]) assert.equal(isBriefing(invalid), false);
  assert.ok(isBriefing({ ...english, future: true, deliveries }));
});

test('severity chips have labels and sections follow decision, now, watch, outlook, money', () => {
  for (const briefing of [english, arabic]) {
    const before = structuredClone(briefing.sections);
    const reversed = [...briefing.sections].reverse();
    assert.deepEqual(orderSections(reversed), briefing.sections);
    assert.deepEqual(briefing.sections, before);
    const withDecision = [...reversed, { ...briefing.sections[0], id: 'decision' as const }];
    assert.equal(orderSections(withDecision)[0].id, 'decision');
  }
  assert.deepEqual(briefingSeverity('critical'), { cls: 'crit', label: 'Critical' });
  assert.deepEqual(briefingSeverity('warning'), { cls: 'warn', label: 'Warning' });
  assert.deepEqual(briefingSeverity('info'), { cls: '', label: 'Info' });
});

test('real channel fixture validates and delivery priority favours Telegram then latest time', () => {
  assert.ok(channels.every(isBriefingChannel));
  assert.ok(channels.find((channel) => channel.channel === 'telegram')?.detail);
  assert.ok(deliveries.every(isBriefingDelivery));
  const telegram = deliveries.find((delivery) => delivery.channel === 'telegram')!;
  const inApp = deliveries.find((delivery) => delivery.channel === 'in_app')!;
  const earlier = { ...telegram, at: new Date(Date.parse(telegram.at) - 60000).toISOString() };
  const input = [inApp, earlier, telegram];
  const before = [...input];
  assert.equal(firstDelivery(input), telegram);
  assert.deepEqual(input, before);
  assert.equal(firstDelivery([inApp]), inApp);
  assert.equal(firstDelivery([]), undefined);
  assert.equal(isBriefingChannel({ ...channels[0], configured: 'yes' }), false);
  assert.equal(isBriefingDelivery({ ...telegram, at: 'invalid' }), false);
});

test('sendBriefing uses the contract body, validates the result and preserves server errors', async (t) => {
  const delivery = deliveries.find((result) => result.channel === 'telegram')!;
  const controller = new AbortController();
  const mock = t.mock.method(globalThis, 'fetch', async (path: string | URL | Request, options?: RequestInit) => {
    assert.equal(path, '/api/v1/briefing/send');
    assert.equal(options?.method, 'POST');
    assert.deepEqual(JSON.parse(String(options?.body)), { lang: english.lang, channel: delivery.channel });
    assert.equal(options?.signal, controller.signal);
    return new Response(JSON.stringify({ delivery, briefing: english }));
  });
  assert.deepEqual(await sendBriefing(english.lang, delivery.channel, controller.signal), { delivery, briefing: english });
  for (const state of ['sent', 'skipped', 'failed'] as const) {
    mock.mock.mockImplementation(async () => new Response(JSON.stringify({ delivery: { ...delivery, state }, briefing: english })));
    assert.equal((await sendBriefing(english.lang, delivery.channel)).delivery.state, state);
  }
  for (const body of [null, {}, { delivery, briefing: { ...english, sections: null } }, { delivery, briefing: arabic }, { delivery: { ...delivery, channel: 'in_app' }, briefing: english }]) {
    mock.mock.mockImplementation(async () => new Response(JSON.stringify(body)));
    await assert.rejects(sendBriefing(english.lang, delivery.channel), /Invalid briefing send result/);
  }
  mock.mock.mockImplementation(async () => new Response(JSON.stringify({ code: 'unavailable', message: 'Briefing unavailable' }), { status: 503 }));
  await assert.rejects(sendBriefing(english.lang, delivery.channel), /Briefing unavailable/);
});
