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
export interface ReadingPayload { tick: number; timestamp: Timestamp; readings: Reading[] }
export interface AssetStatusUpdate { assetId: string; status: AssetStatus; healthScore: number; failureProbability?: number }
export interface Alert {
  id: string; incidentId: string; network: Network; zoneId: string; assetIds: string[];
  severity: 'info' | 'warning' | 'critical'; title: string; summary: string; detectedAt: Timestamp;
  state: 'open' | 'acknowledged' | 'resolved'; confidence: number;
}
export type EvidenceType = 'measured' | 'forecast' | 'rule' | 'assumed';
export interface Reason { text: string; evidenceType: EvidenceType; evidenceRef: string }
export interface CalculationInput { name: string; value: number; unit: string; source: EvidenceType }
export interface Calculation { name: string; formula: string; inputs: CalculationInput[]; result: number; unit: string }
export interface Assumption { name: string; key: string; value: number; unit: string; source: string; userChanged: boolean }
export interface Sensitivity { input: string; changePercent: number; newSaving: number }
export interface Explanation {
  reasons: Reason[]; calculations: Calculation[]; assumptions: Assumption[];
  sensitivity: Sensitivity[]; confidence: number; calculationId: string;
}
export interface CalculationRecord { id: string; incidentId: string; createdAt: Timestamp; cost: Cost; explanation: Explanation }
export interface Cost {
  currency: 'EGP'; actNow: number; delayPerHour: number; expectedSaving: number;
  delayHours: number; costOfDelay: number;
  breakdown: { label: string; amount: number; cause: string }[]; explanation: Explanation;
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
export interface Incident { id: string; title: string; networks: Network[]; zoneIds: string[]; severity: Alert['severity']; alertIds: string[]; findingIds: string[]; calculationId: string | null; state: Alert['state']; openedAt: Timestamp }
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
export interface SnapshotPayload {
  tick: number; timestamp: Timestamp; topology: Topology; latest: Reading[];
  assets: AssetStatusUpdate[]; alerts: Alert[]; incidents: Incident[]; findings: AgentFinding[];
  scenarios: ScenarioState[]; llm: Health['llm'];
}
export interface WsPayloads {
  snapshot: SnapshotPayload; reading: ReadingPayload; asset_status: AssetStatusUpdate; alert: Alert;
  agent_finding: AgentFinding; scenario_state: ScenarioState; forecast_update: Forecast;
  chat_route: ChatRoute; chat_chunk: ChatChunk; chat_done: ChatDone;
}
export type WsEnvelope = { [T in keyof WsPayloads]: { type: T; payload: WsPayloads[T] } }[keyof WsPayloads];
