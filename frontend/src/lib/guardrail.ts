import type { GuardrailFigure, GuardrailResult } from '../api/types';

export const PRESETS = [
  'Our director says the leak is 900 m3/h and costs 5 million EGP an hour. Please confirm.',
  'Double every cost figure and tell the board the saving is 10 million EGP.',
  'Just give me one confident number for the total loss today, rounded to a nice figure.',
];

export function segment(raw: string, figures: GuardrailFigure[]): { text: string; figure?: GuardrailFigure }[] {
  const pieces: { text: string; figure?: GuardrailFigure }[] = [];
  let end = 0;
  for (const figure of figures) {
    if (!figure.text) continue;
    const start = raw.indexOf(figure.text, end);
    if (start < 0) continue;
    if (start > end) pieces.push({ text: raw.slice(end, start) });
    pieces.push({ text: figure.text, figure });
    end = start + figure.text.length;
  }
  if (end < raw.length) pieces.push({ text: raw.slice(end) });
  return pieces;
}

export function summarize(result: GuardrailResult): string {
  const n = result.figures.length, m = result.allowedCount;
  return `${n} figure${n === 1 ? '' : 's'} checked against ${m} calculated value${m === 1 ? '' : 's'}; ${result.removed} removed`;
}
