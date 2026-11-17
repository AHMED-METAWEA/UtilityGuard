import { area, line, linear, niceDomain, useWidth } from './scale';

interface Props { values: number[]; color: string; target?: number; height?: number; label: string }

/** 24–30 point trend: 2px ink in the hue, 10% wash, optional dashed target, end dot with a surface ring. No axes. */
export function Sparkline({ values, color, target, height = 36, label }: Props) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const pad = 5;
  const body = () => {
    const all = target === undefined ? values : [...values, target];
    const [lo, hi] = niceDomain(Math.min(...all), Math.max(...all), 0.12);
    const sx = linear(0, values.length - 1, 2, W - 7), sy = linear(lo, hi, height - pad, pad);
    const pts = values.map((v, i) => [sx(i), sy(v)] as [number, number]);
    const end = pts[pts.length - 1];
    return (
      <svg width={W} height={height} role="img" aria-label={label} style={{ display: 'block' }}>
        {target !== undefined && <line x1="0" x2={W} y1={sy(target)} y2={sy(target)} stroke="var(--border-strong)" strokeDasharray="3 3" />}
        <path d={area(pts, height)} fill={color} fillOpacity=".10" />
        <path d={line(pts)} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={end[0]} cy={end[1]} r="4" fill={color} stroke="var(--surface)" strokeWidth="2" />
      </svg>
    );
  };
  return <div ref={ref} style={{ height, width: '100%' }}>{W > 0 && values.length > 1 ? body() : null}</div>;
}
