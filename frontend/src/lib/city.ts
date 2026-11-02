import type { CityDistrict, CityDistricts, CityFacility, CityStress } from '../api/city';

/** What the district layer can be coloured by. All five are the healthcare team's own 0..100 stress scores (100 is the worst case). */
export type CityLayer = 'overall' | 'air' | 'waste' | 'water' | 'heat';
export const CITY_LAYERS: { id: CityLayer; label: string; short: string }[] = [
  { id: 'overall', label: 'Environmental stress (overall)', short: 'Overall' },
  { id: 'air', label: 'Air quality', short: 'Air' },
  { id: 'waste', label: 'Waste and sanitation', short: 'Waste' },
  { id: 'water', label: 'Water pollution', short: 'Water' },
  { id: 'heat', label: 'Heat', short: 'Heat' },
];

export type Band = 'low' | 'moderate' | 'high';
/** Their Environmental Stress Index bands: below 20 low, 20 to under 35 moderate, 35 and above high. */
export function band(score: number): Band {
  return score >= 35 ? 'high' : score >= 20 ? 'moderate' : 'low';
}
export const BAND_VAR: Record<Band, string> = { low: '--ok', moderate: '--warn', high: '--crit' };
export const BAND_LABEL: Record<Band, string> = { low: 'Low', moderate: 'Moderate', high: 'High' };

export function scoreOf(d: CityDistrict, layer: CityLayer): number {
  return d.metrics.stress[layer as keyof CityStress];
}

/** Circle radius in pixels from the registered residents (area proportional to people, so the eye reads the right size). Clamped so a tiny district stays visible. */
export function radiusFor(residents: number, maxResidents: number): number {
  if (!(maxResidents > 0)) return 8;
  return Math.round(7 + 15 * Math.sqrt(Math.max(0, residents) / maxResidents));
}

/** "2026-09" -> "Sep 2026". */
export function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number);
  if (!y || !m || m < 1 || m > 12) return month;
  return `${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]} ${y}`;
}

export interface Line { label: string; value: string }
const n = (v: number, d = 0) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

/** The raw figures behind a district's colour, grouped by what the layer is about, for the tooltip. */
export function detailLines(d: CityDistrict, layer: CityLayer): Line[] {
  const m = d.metrics;
  const air: Line[] = [{ label: 'Mean AQI', value: n(m.aqiMean, 0) }, { label: 'PM2.5', value: `${n(m.pm25Ugm3, 1)} µg/m³` }, { label: 'PM10', value: `${n(m.pm10Ugm3, 1)} µg/m³` }, { label: 'Days AQI over 150', value: n(m.daysAqiOver150) }];
  const waste: Line[] = [{ label: 'Mean bin fill', value: `${n(m.binFillPct, 1)}%` }, { label: 'Bin-hours at 85% or more', value: `${n(m.binHoursOver85Pct, 1)}%` }, { label: 'Collection efficiency', value: `${n(m.collectionEfficiencyPct, 1)}%` }, { label: 'Uncollected', value: `${n(m.uncollectedTons, 1)} t` }];
  const water: Line[] = [{ label: 'Water pollution index', value: n(m.waterPollutionIndex, 1) }, { label: 'E. coli positive samples', value: `${n(m.ecoliPositivePct, 1)}%` }, { label: 'Chlorine residual', value: `${n(m.chlorineResidualMgL, 2)} mg/L` }, { label: 'Supply interruption', value: `${n(m.supplyInterruptionHours, 1)} h` }];
  const heat: Line[] = [{ label: 'Mean temperature', value: `${n(m.avgTempC, 1)} °C` }, { label: 'Days at 38 °C or more', value: n(m.heatDays) }, { label: 'Heat-related visits', value: n(m.heatRelatedVisits) }];
  const overall: Line[] = [{ label: 'Air score', value: n(m.stress.air, 1) }, { label: 'Waste score', value: n(m.stress.waste, 1) }, { label: 'Water score', value: n(m.stress.water, 1) }, { label: 'Heat score', value: n(m.stress.heat, 1) }];
  return { overall, air, waste, water, heat }[layer];
}

/** Where the map draws a district or facility: on the zone of the twin it is matched to (`placedAt`). Their coordinates are approximate and far from our zones, so
 * the twin's geography is the one that is drawn, and a district with no match (the park) is not drawn at all rather than put somewhere it does not belong. */
export function drawnAt(p: { placedAt?: { lat: number; lng: number } }): { lat: number; lng: number } | null {
  return p.placedAt ?? null;
}

/** How a district of the healthcare project relates to the twin's own zones, in words. The match is by name and kind; the symbol is drawn on the zone's ground
 * for readability only, because their coordinates are approximate (their government district is about 5 km from ours). */
export function matchNote(d: CityDistrict): string {
  const zones = d.ourZoneIds.join(', ');
  if (d.matchKind === 'direct') return `Matched to ${zones} by name and kind; drawn on that zone, position approximate`;
  if (d.matchKind === 'type') return `No zone of the twin has this name; read as the same kind of district (${zones}); drawn on one of them, position approximate`;
  return 'No counterpart in the twin';
}

export function facilityLine(d: CityDistrict): string {
  const f = d.facilities;
  return `${f.hospitals} hospital${f.hospitals === 1 ? '' : 's'}, ${f.clinics} clinic${f.clinics === 1 ? '' : 's'}, ${f.doctors} specialist${f.doctors === 1 ? '' : 's'}`;
}

/** The bounds that hold every district and facility, as [west, south, east, north], so the map can show both projects' places at once. */
export function cityBounds(districts: CityDistrict[], facilities: CityFacility[]): [number, number, number, number] | null {
  const pts = [...districts.map(drawnAt), ...facilities.map(drawnAt)].filter((p): p is { lat: number; lng: number } => p !== null);
  if (!pts.length) return null;
  return [Math.min(...pts.map((p) => p.lng)), Math.min(...pts.map((p) => p.lat)), Math.max(...pts.map((p) => p.lng)), Math.max(...pts.map((p) => p.lat))];
}

export function isCityDistricts(v: unknown): v is CityDistricts {
  const o = v as CityDistricts | null;
  return !!o && typeof o.month === 'string' && Array.isArray(o.months) && Array.isArray(o.districts) && o.districts.every((d) =>
    typeof d.districtKey === 'string' && typeof d.position?.lat === 'number' && typeof d.position?.lng === 'number' &&
    ['direct', 'type', 'none'].includes(d.matchKind) && typeof d.metrics?.stress?.overall === 'number' && typeof d.facilities?.hospitals === 'number');
}
