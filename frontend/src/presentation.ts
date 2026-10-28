import type { Alert, Calculation } from './api/types';

export const isOpenAlert = (alert: Alert) => alert.state === 'open' || alert.state === 'acknowledged';

/** Text substitution only: monetary results always come from the backend. */
export function substitutedFormula(calculation: Calculation): string {
  const inputs = new Map(calculation.inputs.map((input) => [input.name, input]));
  return calculation.formula.replace(/[A-Za-z_][A-Za-z_0-9]*/g, (token) => {
    const input = inputs.get(token);
    return input ? `${input.value.toLocaleString('en', { maximumFractionDigits: 2 })} ${input.unit}` : token;
  });
}

export function keyCalculations(calculations: Calculation[]): Calculation[] {
  const main = calculations.find((c) => /loss/i.test(c.name) && !/delay/i.test(c.name)) ?? calculations[0];
  const delay = calculations.find((c) => /delay.*hour/i.test(c.name));
  const saving = calculations.find((c) => /saving/i.test(c.name));
  const chosen = [...new Set([main, delay, saving].filter((c): c is Calculation => Boolean(c)))];
  return [...chosen, ...calculations.filter((c) => !chosen.includes(c))].slice(0, 3);
}
