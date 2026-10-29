import { useEffect, useRef, useState } from 'react';

export const linear = (d0: number, d1: number, r0: number, r1: number) => {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v: number) => r0 + (v - d0) * k;
};

/** "Nice" tick values covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(1, count - 1);
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return out;
}
export const niceDomain = (lo: number, hi: number, pad = 0.06): [number, number] => {
  const span = (hi - lo) || Math.abs(hi) || 1;
  const t = niceTicks(lo - span * pad, hi + span * pad, 5);
  return [t[0] ?? lo, t[t.length - 1] ?? hi];
};

export const line = (pts: [number, number][]) => (pts.length ? 'M' + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join('L') : '');
export const area = (pts: [number, number][], base: number) =>
  pts.length ? `${line(pts)}L${pts[pts.length - 1][0].toFixed(1)},${base}L${pts[0][0].toFixed(1)},${base}Z` : '';
export const band = (hi: [number, number][], lo: [number, number][]) =>
  hi.length ? `${line(hi)}L${[...lo].reverse().map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join('L')}Z` : '';

/** Column with a 4px rounded data-end and a square baseline. */
export const colPath = (x: number, y: number, w: number, base: number, r = 4) => {
  const h = base - y;
  if (h <= 0) return '';
  const rr = Math.min(r, w / 2, h);
  return `M${x},${base}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${base}Z`;
};

export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el); setW(Math.round(el.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
