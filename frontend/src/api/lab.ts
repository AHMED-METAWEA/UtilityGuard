import type { Assumption, Calculation, Network } from './types';

export type DetectorId = 'd1' | 'd2' | 'd3' | 'd4';
export interface DetectorFaultScore { kind: string; label: string; runs: number; detected: number; detectionRate: number; medianDelayMin?: number | null; meanDelayCost: number }
export interface DetectorScore {
  id: DetectorId; name: string; kind: 'baseline' | 'statistical' | 'unsupervised' | 'supervised'; note: string; falseAlarmsPer100H: number;
  detectionRate: number; medianDelayMin?: number | null; p90DelayMin?: number | null; meanDelayCost: number;
  unseenDetectionRate?: number | null; unseenMedianDelayMin?: number | null; byFault: DetectorFaultScore[];
}
export interface LeakPoint { leakM3H: number; results: { detectorId: DetectorId; detectionRate: number; medianDelayMin?: number | null }[] }
export interface LabBenchmark {
  generatedAt: string; bestDetectorId: DetectorId; notes: string[]; detectors: DetectorScore[]; leakCurve: LeakPoint[];
  faults: { kind: string; label: string; runs: number; hourlyCost: number }[];
  config: { tickMinutes: number; windowMinutes: number; expectationErrorPct: number; trainRuns: number; testRuns: number; cleanHours: number; holdoutZones: string[]; seed: number };
}
export interface LocalizationCandidate { rank: number; assetId: string; name: string; zoneId: string; score: number; leakM3H: number }
export interface Localization { method: string; sensorsUsed: number; sensorsTotal: number; demandErrorPct: number; candidates: LocalizationCandidate[]; ambiguous: boolean; incidentId?: string }
export interface LabLocalization { bySize: { leakM3H: number; top1: number; top3: number; runs: number }[]; top1: number; top3: number; runs: number; sensorsTotal: number }
export interface SensorValueRow { assetId: string; name: string; kind: string; sensors: string[]; detectionRate: number; medianDelayMin?: number | null; extraDelayMin: number; extraCost: number; localizationTop1: number }
export interface SensorValue { detectorId: DetectorId; leakM3H: number; runsPerAsset: number; baselineDetectionRate: number; baselineDelayMin?: number | null; baselineLocalizationTop1: number; hourlyCost: number; rows: SensorValueRow[] }
export interface TrialResult {
  truthAssetId: string; truthName: string; leakM3H: number; windowMin: number; hourlyCost: number;
  detectors: { detectorId: DetectorId; name: string; detected: boolean; delayMin?: number | null }[];
  localization: Localization; hitRank?: number | null; disabledSensors: string[];
}
export interface LabQuality {
  kinds: { kind: string; label: string; runs: number; detected: number; detectionRate: number; medianDelayMin?: number | null }[];
  falseFlagsPer100SensorHours: number; sensorHours: number; processRuns: number; processFalseFlags: number; windowTicks: number;
}
export interface QualityNow { sensorsTotal: number; flagged: { sensorId: string; status: 'missing' | 'stuck' | 'drift'; detail: string }[] }

export type OptionId = 'fix_now' | 'isolate_fix' | 'wait_6h' | 'wait_24h' | 'wait_72h';
export interface DecisionOption {
  id: OptionId; name: string; delayHours: number; currentCost: number; expectedCost: number; p10: number; p50: number; p90: number;
  expectedRegret: number; winShare: number; winShareText: string; costLines: Calculation[]; netBenefitVsFixNow: number;
}
export interface BreakEven { optionId: OptionId; parameter: 'leak_m3h' | 'delay_per_hour'; value?: number | null; unit: string; formula: string; reason?: string | null }
export interface DecisionMap {
  xKey: string; xName: string; xUnit: string; xValues: number[]; yKey: string; yName: string; yUnit: string; yValues: number[];
  cells: (OptionId | null)[][]; currentX: number; currentY: number; currentOptionId?: OptionId | null;
}
export interface DecisionReport {
  incidentId: string; currency: 'EGP'; recommendedOptionId?: OptionId | null; runs: number; seed: number; assumptions: Assumption[];
  options: DecisionOption[]; breakEven: BreakEven[]; decisionMap: DecisionMap; review: boolean; reviewReason?: string | null;
}
export type { Network };
