const nf = (digits: number) => new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const cache = new Map<number, Intl.NumberFormat>();
export const fmt = (n: number | null | undefined, digits = 0) => {
  if (n === null || n === undefined || !Number.isFinite(n)) return '–';
  let f = cache.get(digits); if (!f) { f = nf(digits); cache.set(digits, f); }
  return f.format(n);
};
export const egp = (n: number | null | undefined) => `${fmt(n)} EGP`;
export const compact = (n: number) => {
  const a = Math.abs(n);
  if (a >= 1e6) return `${fmt(n / 1e6, a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e4) return `${fmt(n / 1e3, 0)}k`;
  if (a >= 1e3) return `${fmt(n / 1e3, 1)}k`;
  return fmt(n, a < 10 ? 1 : 0);
};
export const pct = (x: number, digits = 0) => `${fmt(x * 100, digits)}%`;
export const clock = (iso: string | number | Date, seconds = true) =>
  new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), timeZone: 'Africa/Cairo' });
export const hhmm = (iso: string | number | Date) => clock(iso, false);
export const ago = (iso: string, now = Date.now()) => {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
};
/** Human label for a sensor id such as water-pipe-04-flow-in. */
export const sensorLabel = (id: string) => id.replace(/^(water|elec|cool)-/, '').replace(/-/g, ' ');
