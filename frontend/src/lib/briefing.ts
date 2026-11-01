import type { Briefing, BriefingChannel, BriefingDelivery } from '../api/types';

const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string';
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const timestamp = (v: unknown) => text(v) && Number.isFinite(Date.parse(v));
const channel = (v: unknown) => v === 'telegram' || v === 'in_app';
const SECTIONS: Briefing['sections'][number]['id'][] = ['decision', 'now', 'watch', 'outlook', 'money'];
const SEVERITY = {
  critical: { cls: 'crit', label: 'Critical' },
  warning: { cls: 'warn', label: 'Warning' },
  info: { cls: '', label: 'Info' },
};

export const isBriefingDelivery = (v: unknown): v is BriefingDelivery => record(v) && channel(v.channel)
  && ['sent', 'failed', 'skipped'].includes(String(v.state)) && text(v.detail) && timestamp(v.at);
export const isBriefingChannel = (v: unknown): v is BriefingChannel => record(v) && channel(v.channel)
  && typeof v.configured === 'boolean' && text(v.detail);

export const isBriefing = (v: unknown): v is Briefing => record(v) && timestamp(v.generatedAt)
  && (v.lang === 'en' || v.lang === 'ar') && text(v.headline) && text(v.text)
  && Array.isArray(v.sections) && v.sections.every((section) => record(section) && SECTIONS.some((id) => id === section.id)
    && text(section.title) && Array.isArray(section.items) && section.items.every((item) => record(item)
      && text(item.kind) && ['critical', 'warning', 'info'].includes(String(item.severity)) && text(item.text) && text(item.fingerprint)
      && (item.link === undefined || (text(item.link) && /^#\/(live|incidents|savings|forecast|lab|executive|audit)(\/[^\s/?#]+)?$/.test(item.link)))))
  && Array.isArray(v.figures) && v.figures.every((figure) => record(figure) && text(figure.key) && text(figure.label) && text(figure.display) && number(figure.value))
  && number(v.repeated) && Number.isInteger(v.repeated) && v.repeated >= 0
  && Array.isArray(v.deliveries) && v.deliveries.every(isBriefingDelivery);

export const briefingSeverity = (severity: Briefing['sections'][number]['items'][number]['severity']) => SEVERITY[severity];
export const orderSections = (sections: Briefing['sections']): Briefing['sections'] => [...sections].sort((a, b) => SECTIONS.indexOf(a.id) - SECTIONS.indexOf(b.id));

/** Prefer the Telegram outcome, then the newest result within a channel. */
export function firstDelivery(deliveries: readonly BriefingDelivery[]): BriefingDelivery | undefined {
  return [...deliveries].sort((a, b) => Number(b.channel === 'telegram') - Number(a.channel === 'telegram') || Date.parse(b.at) - Date.parse(a.at))[0];
}
