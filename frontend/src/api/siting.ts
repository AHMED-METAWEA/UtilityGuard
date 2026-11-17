import { request } from './client';
import { useFetch, type Fetched } from './dash';

/** The siting what-if: a facility put in a zone of a separate copy of the twin, read with and without it (contract 4.23). Probabilities are shares of simulated days under the stated assumptions. */
export type FacilityKind = 'hospital' | 'hospital_expansion' | 'clinic' | 'custom';
export type SitingNetwork = 'electricity' | 'water' | 'cooling';
export type SitingCondition = 'normal' | 'heat' | 'substation_loss';

export interface SitingAssumption { name: string; value: number; low: number; high: number; unit: string; basis: string }
export interface SitingOptions {
  zones: { id: string; name: string; type: string; substation: string; pump: string; coolingPlant?: string }[];
  kinds: { id: FacilityKind; label: string; unit: string; defaultUnits: number; capexMEgp?: number; capexBasis: string; assumptions: SitingAssumption[] }[];
  limits: Record<SitingNetwork, string>; spread: number;
}
export interface SitingRequest {
  zoneId: string; kind: FacilityKind; units: number; waterM3H?: number; electricityMw?: number; coolingMw?: number; capexMEgp?: number; runs?: number;
}
export interface NetworkRisk {
  network: SitingNetwork; asset?: string; limit: string; probabilityBefore: number; probabilityAfter: number; watchBefore: number; watchAfter: number;
  utilisationBeforeP95?: number; utilisationAfterP95?: number; utilisationAfterMax?: number;
}
export interface ConditionResult {
  condition: SitingCondition; label: string; days: number; risks: NetworkRisk[]; anyBefore: number; anyAfter: number;
  extraUnservedMwhPerDay: number; extraUnservedCostEgpPerDay: number; extraUnservedCostLowEgpPerDay: number; extraUnservedCostHighEgpPerDay: number;
}
export interface Mitigation { network: SitingNetwork; package: string; assets: string[]; costMEgp: number; reason: string }
export interface SitingCost { capexMEgp?: number; capexBasis: string; mitigations: Mitigation[]; mitigationMEgp: number; totalMEgp?: number; note: string }
export interface SitingDistrict { districtKey: string; districtCode: string; matchKind: 'direct' | 'type' | 'none'; hospitals: number; theirPressureBaseline?: number; theirOverCapacity?: boolean; esiOverall?: number }
export interface SitingResult {
  zone: { id: string; name: string; type: string; substation: string; pump: string; coolingPlant?: string };
  kind: FacilityKind; units: number; unit: string;
  load: { waterM3H: number; electricityMw: number; coolingMw: number; onSiteChillersMw: number };
  assumptions: SitingAssumption[]; conditions: ConditionResult[]; cost: SitingCost; districts: SitingDistrict[]; verdict: string; runs: number; seed: number; simulated: boolean;
}

export const useSitingOptions = (): Fetched<SitingOptions> => useFetch<SitingOptions>('/siting/options');
export const evaluateSiting = (body: SitingRequest, signal?: AbortSignal) =>
  request<SitingResult>('/siting/evaluate', { method: 'POST', body: JSON.stringify(body), signal: signal ?? AbortSignal.timeout(60000) });
