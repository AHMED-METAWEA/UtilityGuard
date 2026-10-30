/** Contract sections 4–7; regenerated fixtures are the wire-format reference. */
export type Network = 'water' | 'electricity' | 'cooling';
export type Agent = Network | 'orchestrator';
export type Timestamp = string;
export type AssetStatus = 'normal' | 'warning' | 'fault' | 'offline';
export type Quality = 'ok' | 'suspect' | 'missing';
export interface Position { lat: number; lng: number }
export interface Zone {
  id: string; name: string; type: 'government' | 'business' | 'residential' | 'services';
  center: Position; polygon: Position[];
}
export interface Geometry { type: 'point' | 'line'; coordinates: Position[] }
export interface Asset {
  id: string; network: Network; kind: 'pipe' | 'pump' | 'reservoir' | 'substation' | 'feeder' | 'transformer' | 'plant' | 'loop';
  name: string; zoneId: string; geometry: Geometry; status: AssetStatus;
}
export interface Sensor {
  id: string; assetId: string; network: Network; kind: 'pressure' | 'flow' | 'load' | 'voltage' | 'temperature';
  unit: string; normalRange: { min: number; max: number }; position: Position;
}
export interface Dependency { fromAsset: string; toAsset: string; relation: string }
export interface Topology { zones: Zone[]; assets: Asset[]; sensors: Sensor[]; links: Dependency[] }
export interface Reading { sensorId: string; timestamp: Timestamp; value: number; quality: Quality; forecast?: number }
export interface ModelComparisonRow {
  name: string; kind: 'gradient boosting' | 'linear' | 'random forest' | 'statistical' | 'ensemble';
  mae: number; rmse: number; mape: number; bias: number; oofMae: number; skillVsBestBaseline: number; fitSeconds: number;
  isLive: boolean; isSelected: boolean; params: string; pLgbmBetter: number; hourlyMape: number[];
  weights: Record<string, number>; weightsSource: string;
}
export interface ReadingPayload { tick: number; timestamp: Timestamp; readings: Reading[] }
export interface AssetStatusUpdate { assetId: string; status: AssetStatus; healthScore: number; failureProbability?: number }
export interface Alert {
  id: string; incidentId: string; network: Network; zoneId: string; assetIds: string[];
  severity: 'info' | 'warning' | 'critical'; title: string; summary: string; detectedAt: Timestamp;
  state: 'open' | 'acknowledged' | 'resolved'; confidence: number;
}
export type EvidenceType = 'measured' | 'forecast' | 'rule' | 'assumed';
export interface Reason { text: string; evidenceType: EvidenceType; evidenceRef: string }
export type AssumptionStatus = 'official' | 'benchmark' | 'engineering' | 'planning';
export interface CalculationInput { name: string; value: number; unit: string; source: EvidenceType; key?: string; basis?: string; status?: AssumptionStatus }
export interface Calculation { name: string; formula: string; inputs: CalculationInput[]; result: number; unit: string }
export type ReferenceKind = 'official' | 'reported' | 'aggregator' | 'benchmark' | 'historical' | 'related';
export interface PriceReference { label: string; low: number | null; high: number | null; unit: string; kind: ReferenceKind; publisher: string; url: string; asof: string; note: string }
export type ReferenceEvidence = 'consistent' | 'partly' | 'differs' | 'related' | 'none';
export interface Assumption {
  name: string; key: string; value: number; unit: string; source: string; userChanged: boolean;
  basis: string; status: AssumptionStatus; low: number; high: number; reviewed: string;
  references?: PriceReference[]; evidence?: ReferenceEvidence; searchNote?: string | null;
}
export interface TornadoRow { input: string; name: string; low: number; high: number; savingAtLow: number; savingAtHigh: number }
export interface Sensitivity { input: string; changePercent: number; newSaving: number }
export interface Explanation {
  reasons: Reason[]; calculations: Calculation[]; assumptions: Assumption[];
  sensitivity: Sensitivity[]; tornado?: TornadoRow[]; confidence: number; calculationId: string;
}
export interface CalculationRecord { id: string; incidentId: string; createdAt: Timestamp; cost: Cost; explanation: Explanation }
export type CalcInputLike = CalculationInput;
export interface Cost {
  currency: 'EGP'; actNow: number; delayPerHour: number; expectedSaving: number;
  delayHours: number; costOfDelay: number; responseHours?: number; actNowLosses?: number; review?: boolean; reviewReason?: string; savingRange?: { p10: number; p50: number; p90: number; runs: number };
  breakdown: { label: string; amount: number; cause: string; group?: string }[]; explanation: Explanation;
}
export interface AgentFinding {
  id: string; incidentId: string; agent: Agent; headline: string; explanation: string;
  probableCauses: string[]; predictedImpact: { text: string; zoneIds: string[] };
  recommendedAction: { text: string; crewType: string; urgency: 'immediate' | 'within_hours' | 'scheduled' }; cost: Cost; reasoningSteps: string[];
  /** The fixtures disambiguate the explanation block from the plain-language string. */
  explain: Explanation; createdAt: Timestamp; fallback: boolean; numberCheck: 'passed' | 'corrected';
}
export interface ForecastPoint { timestamp: Timestamp; predicted: number; lower: number; upper: number }
export interface ForecastMetrics { mae: number; rmse: number; mape: number }
export interface FeatureImportance { feature: string; importance: number }
export interface Forecast {
  network: Network; zoneId: string; unit: string; horizonHours: number; points: ForecastPoint[]; metrics: ForecastMetrics; topFeatures: FeatureImportance[];
}
export interface ForecastQuery { network: Network; zoneId: string; horizonHours: number }
export interface ModelMetrics {
  forecasting: (ForecastMetrics & { network: Network; unit: string })[]; detection: { precision: number; recall: number; f1: number };
  lastRun: Timestamp; featureImportances: Record<string, FeatureImportance[]>;
}
export type Parameters = Record<string, string | number | boolean>;
export interface ScenarioParameter { name: string; type: 'asset' | 'number' | 'select'; default: string | number | boolean; min?: number; max?: number; options?: (string | { label: string; value: string })[] }
export interface Scenario { id: string; name: string; description: string; network: Network | 'multi'; parameters: ScenarioParameter[] }
export interface ScenarioRunRequest { scenarioId: string; parameters: Parameters }
export interface ScenarioRunResponse { runId: string; scenarioId: string; startedAt: Timestamp }
export interface ScenarioStopRequest { runId: string }
export interface ScenarioState { runId: string; scenarioId: string; state: 'running' | 'stopped'; startedAt: Timestamp }
export interface ScenarioResetResponse { status: string }
export interface ChatRequest { message: string; incidentId?: string }
export interface ChatAccepted { messageId: string }
export interface RoutingStep { agent: Agent; reason: string }
export interface ChatRoute { messageId: string; routing: RoutingStep[] }
export interface ChatChunk { messageId: string; text: string; agent: Agent }
export interface ChatDone { messageId: string; agentsConsulted: Agent[]; routing: RoutingStep[]; fallback: boolean; calculationId: string | null; findingIds: string[] }
export interface ChatMessage {
  messageId: string; role: 'user' | 'assistant'; text: string; complete: boolean;
  routing: RoutingStep[]; agentsConsulted: Agent[]; fallback: boolean; activeAgent?: Agent; calculationId?: string | null; findingIds?: string[];
}
export interface Health { status: string; llm: 'live' | 'fallback'; tick?: number; seed?: number; activeRuns?: string[] }
export interface Incident { id: string; title: string; networks: Network[]; zoneIds: string[]; severity: Alert['severity']; alertIds: string[]; findingIds: string[]; calculationId: string | null; state: Alert['state']; openedAt: Timestamp; reanalysing?: boolean }
export interface AcknowledgeResponse { id: string; state: 'acknowledged' }
export type IncidentReport = Blob;
export interface ComparisonResult { label: string; totalCost: number; waterLostM3: number; outageHours: number; series: ForecastPoint[] }
export interface Comparison { scenarioId: string; withoutIntervention: ComparisonResult; withIntervention: ComparisonResult; saving: number }
export interface SensorPlacementSuggestion { network: Network; nearAssetId: string; kind: Sensor['kind']; position: Position; detectionImprovement: number; reason: string }
export type SensorPlacement = SensorPlacementSuggestion[];
export interface DriftIndicator { sensorId: string; unit: string; drift: number; status: string }
export type Drift = DriftIndicator[];
export type AssumptionsConfiguration = Assumption[];
export interface AssumptionsUpdate { assumptions: Assumption[]; recomputed: number }
export interface ApiError { code: string; message: string }
export interface BoardFact { label: string; value: number; unit: string; source: 'measured' | 'calculated' | 'assumed' }
export interface BoardMessage {
  id: string; sessionId: string; round: number; agent: Agent;
  kind: 'opening' | 'position' | 'dependent' | 'challenge' | 'decision' | 'concession';
  text: string; facts: BoardFact[]; source: 'llm' | 'template';
}
export interface BoardTask {
  id: string; agent: Network; assetId: string; assetName: string; kind: string; action: string; crew: string;
  hoursToFix: number; hourlyCost: number; repairCost: number; returnPerCrewHour: number; relievedBy: string[];
}
export interface BoardStep {
  order: number; taskId: string; agent: Network; assetName: string; action: string; crewSlot: number;
  startHour: number; endHour: number; hourlyCost: number; lossUntilFixed: number; repairCost: number;
  repairMultiplier: number; dropped: boolean; reliefHour?: number | null;
}
export interface BoardAlternative { id: string; name: string; order: string[]; expectedCost: number; extraCost: number }
export interface BoardFlip { taskId: string; assetName: string; current: number; threshold: number; direction: 'below' | 'above' }
export interface BoardRisk { kind: 'overload' | 'queue' | 'tail'; text: string }
export interface BoardPlan {
  steps: BoardStep[]; expectedCost: number; p10: number; p50: number; p90: number; winShare: number;
  regretMean: number; regretP90: number; hourlyBleed: number; runs: number;
}
export type DecisionStatus = 'proposed' | 'executing' | 'completed' | 'rejected' | 'superseded' | 'stale';
export interface Review {
  outcome: 'approved' | 'rejected'; operator: string; at: Timestamp; note: string; override: boolean; order: string[];
  expectedCost?: number | null; boardCost?: number | null; costDelta?: number | null; decisionHash: string; secondsPerHour?: number | null;
}
export interface ExecutionStep {
  taskId: string; assetId: string; assetName: string; action: string; crewSlot: number;
  planStartHour: number; planEndHour: number; state: 'waiting' | 'working' | 'done' | 'skipped'; note: string;
  startedAt?: Timestamp | null; finishedAt?: Timestamp | null; confirmedAt?: Timestamp | null;
}
export interface AuditEntry {
  seq: number; at: Timestamp; run: string; actor: string; action: string; sessionId?: string | null; subject?: string | null;
  detail: string; data: Record<string, string | number | boolean>; prevHash: string; hash: string;
}
export interface AuditVerification { ok: boolean; entries: number; brokenAt?: number | null; head?: string | null }
export interface OrderPreview { order: string[]; steps: BoardStep[]; expectedCost: number; boardCost: number; costDelta: number; override: boolean }
export interface ReviewRequest { operator: string; note?: string; order?: string[] | null; secondsPerHour?: number | null }
export interface BoardDecision {
  sessionId: string; incidentIds: string[]; createdAt: Timestamp; crews: number; tasks: BoardTask[]; plan: BoardPlan;
  alternatives: BoardAlternative[]; flips: BoardFlip[]; risks: BoardRisk[]; extraCrewSaving?: number | null;
  status: DecisionStatus; review?: Review | null; execution: ExecutionStep[];
  conflict: boolean; summary: string; summarySource: BoardMessage['source']; calculationId?: string | null;
}
export interface BoardSession {
  id: string; incidentId?: string | null; state: 'running' | 'done'; startedAt: Timestamp;
  messages: BoardMessage[]; decision?: BoardDecision | null;
}
export interface BoardState { sessionId: string; incidentId?: string | null; state: BoardSession['state']; startedAt: Timestamp }
export interface SnapshotPayload {
  tick: number; timestamp: Timestamp; topology: Topology; latest: Reading[];
  assets: AssetStatusUpdate[]; alerts: Alert[]; incidents: Incident[]; findings: AgentFinding[];
  scenarios: ScenarioState[]; llm: Health['llm'];
}
export interface WsPayloads {
  snapshot: SnapshotPayload; reading: ReadingPayload; asset_status: AssetStatusUpdate; alert: Alert;
  incident: Incident; agent_finding: AgentFinding; scenario_state: ScenarioState; forecast_update: Forecast;
  chat_route: ChatRoute; chat_chunk: ChatChunk; chat_done: ChatDone;
  board_state: BoardState; board_message: BoardMessage; board_decision: BoardDecision; audit_entry: AuditEntry; early_warning: EarlyWarning;
}
export type WsEnvelope = { [T in keyof WsPayloads]: { type: T; payload: WsPayloads[T] } }[keyof WsPayloads];

// ---------- early warning, "why this number?", guardrail (contract 4.15 to 4.17) ----------
export interface WarningSignal { label: string; value: number; peerValue: number; unit: string }
export interface WarningCost { unplanned: number; planned: number; breakEven: number; risk: number; verdict: 'maintain_now' | 'watch'; expectedSaving: number }
export interface RiskPoint { t: string; risk: number; peer: number }
export interface EarlyWarning {
  id: string; assetId: string; assetName: string; network: Network; zoneId: string; state: 'active' | 'cleared' | 'failed';
  createdAt: string; updatedAt: string; risk: number; peerRisk: number; excess: number; risePerMinute?: number; minutesTo90?: number;
  signals: WarningSignal[]; series: RiskPoint[]; cost: WarningCost; text: string; leadTimeSeconds?: number; whyRef: string;
}
export interface WhyInput { name: string; value: number; unit: string; source: 'measured' | 'forecast' | 'rule' | 'assumed'; key?: string; basis: string; status?: string }
export interface WhyRecord {
  ref: string; title: string; value?: number; unit: string; evidence: 'measured' | 'calculated' | 'model' | 'assumed' | 'forecast';
  formula: string; steps: string[]; inputs: WhyInput[]; notes: string[]; links: { label: string; href: string }[]; computedAt: string;
}
export interface GuardrailFigure { text: string; value: number; verdict: 'verified' | 'removed'; matched?: string; matchedValue?: number }
export interface GuardrailResult {
  prompt: string; incidentId: string; calculationId: string; source: 'llm' | 'planted'; raw: string; checked: string;
  figures: GuardrailFigure[]; removed: number; allowedCount: number;
}
