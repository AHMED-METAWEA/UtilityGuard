import type { WhyRecord } from '../api/types';

export const kpiRef = (id: string) => `kpi:${id}`;
export const healthRef = (assetId: string) => `asset-health:${assetId}`;
export const riskRef = (assetId: string) => `asset-risk:${assetId}`;
export const savingRef = (id: string) => `saving:${id}`;
export const forecastRef = (network: string) => `forecast:${network}`;
export const warningRef = (id: string) => `warning:${id}`;

const KPI: Record<string, string> = {
  nrw: 'Non-revenue water', water_production: 'Water production', electric_load: 'Electric load',
  cooling_efficiency: 'Cooling efficiency', districts_served: 'Districts served', savings_available: 'Savings available',
};
const words = (s: string) => s.replace(/[_-]/g, ' ');

export function describeRef(ref: string): string {
  const colon = ref.indexOf(':');
  const kind = ref.slice(0, colon), id = ref.slice(colon + 1);
  if (colon < 0 || !id) return words(ref);
  switch (kind) {
    case 'kpi': return KPI[id] ?? words(id);
    case 'asset-health': return `Health of ${id}`;
    case 'asset-risk': return `Risk of ${id}`;
    case 'saving': return `Monthly saving for ${words(id)}`;
    case 'forecast': return `${words(id).replace(/^./, (c) => c.toUpperCase())} forecast error (MAPE)`;
    case 'warning': return `Early warning ${id}`;
    default: return ref;
  }
}

export function whyError(error: unknown): string {
  if (error instanceof Error) {
    if ('code' in error && (error.code === 'no_explanation' || error.code === '404')) return 'There is no explanation for this number yet.';
    return error.message;
  }
  return 'Could not load this explanation.';
}

/** REST records use the same input sources as calculation records. Allow future fields. */
export function isWhyRecord(value: unknown): value is WhyRecord {
  if (!value || typeof value !== 'object') return false;
  const r = value as Record<string, unknown>;
  const strings = (v: unknown) => Array.isArray(v) && v.every((x) => typeof x === 'string');
  return typeof r.ref === 'string' && typeof r.title === 'string' && typeof r.unit === 'string'
    && (r.value === undefined || (typeof r.value === 'number' && Number.isFinite(r.value)))
    && ['measured', 'calculated', 'model', 'assumed', 'forecast'].includes(r.evidence as string)
    && typeof r.formula === 'string' && strings(r.steps) && strings(r.notes)
    && typeof r.computedAt === 'string' && Number.isFinite(Date.parse(r.computedAt))
    && Array.isArray(r.links) && r.links.every((x) => x && typeof x.label === 'string' && typeof x.href === 'string')
    && Array.isArray(r.inputs) && r.inputs.every((x) => x && typeof x.name === 'string'
      && typeof x.value === 'number' && Number.isFinite(x.value) && typeof x.unit === 'string'
      && ['measured', 'forecast', 'rule', 'assumed'].includes(x.source) && typeof x.basis === 'string'
      && (x.key === undefined || typeof x.key === 'string') && (x.status === undefined || typeof x.status === 'string'));
}
