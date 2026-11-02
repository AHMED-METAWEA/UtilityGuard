import { useFetch, type Fetched } from './dash';

/** The healthcare team's districts, facilities and environment (contract 4.22). Read-only: the twin did not generate these values. */
export interface CityStress { air: number; waste: number; water: number; heat: number; overall: number }
export interface CityMetrics {
  month: string; residents: number;
  aqiMean: number; aqiMax: number; pm25Ugm3: number; pm10Ugm3: number; daysAqiOver150: number;
  binFillPct: number; binHoursOver85Pct: number; overflowIncidents: number; collectionEfficiencyPct: number; uncollectedTons: number; odorPestRisk: number;
  waterPollutionIndex: number; ecoliPositivePct: number; chlorineResidualMgL: number; turbidityNtu: number; supplyInterruptionHours: number; leakIncidents: number; nonRevenueWaterPct: number;
  avgTempC: number; heatDays: number;
  stress: CityStress; visitsDemand: number; respiratoryVisits: number; waterborneVisits: number; heatRelatedVisits: number;
}
export interface FacilityCounts { hospitals: number; clinics: number; doctors: number }
export type MatchKind = 'direct' | 'type' | 'none';
export interface CityDistrict {
  districtKey: string; code: string; nameAr: string; kind: string; areaKm2: number; position: { lat: number; lng: number }; placedAt?: { lat: number; lng: number };
  matchKind: MatchKind; ourZoneIds: string[]; facilities: FacilityCounts; metrics: CityMetrics;
}
export interface CitySource { repository: string; commit: string; sha256: string; simulated: boolean; note: string }
export interface CityDistricts { month: string; months: string[]; source: CitySource; districts: CityDistrict[] }
export interface CityFacility { facilityId: string; kind: 'hospital' | 'clinic' | 'doctors'; nameAr: string; districtKey: string; position: { lat: number; lng: number }; placedAt?: { lat: number; lng: number } }

/** `month` null asks for the latest. Nothing is requested until `enabled`, so the map costs nothing when the layer is off. */
export function useCityDistricts(enabled: boolean, month: string | null): Fetched<CityDistricts> {
  return useFetch<CityDistricts>(enabled ? (month ? `/city/districts?month=${encodeURIComponent(month)}` : '/city/districts') : null);
}
export function useCityFacilities(enabled: boolean): Fetched<CityFacility[]> {
  return useFetch<CityFacility[]>(enabled ? '/city/facilities' : null);
}
