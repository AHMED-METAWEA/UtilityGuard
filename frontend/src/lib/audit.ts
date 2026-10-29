import type { AuditEntry } from '../api/types';

export const AUDIT_GROUPS = ['Proposals', 'Decisions', 'Crew work', 'Detector', 'System'] as const;
export type AuditGroup = typeof AUDIT_GROUPS[number];
const ACTIONS: Record<string, [AuditGroup, string]> = {
  proposed: ['Proposals', 'Plan proposed'], superseded: ['Proposals', 'Proposal replaced'], proposal_stale: ['Proposals', 'Proposal cleared'],
  approve: ['Decisions', 'Plan approved'], approve_with_changes: ['Decisions', 'Own order approved'], reject: ['Decisions', 'Plan rejected'],
  crew_dispatched: ['Crew work', 'Crew started work'], repair_done: ['Crew work', 'Repair finished'], no_crew_needed: ['Crew work', 'No crew needed'],
  skipped: ['Crew work', 'Job skipped'], plan_completed: ['Crew work', 'Plan completed'], alert_cleared: ['Detector', 'Detector confirmed clearance'],
  simulation_reset: ['System', 'Twin reset'],
};
export const auditGroup = (action: string): AuditGroup => ACTIONS[action]?.[0] ?? 'System';
export const auditLabel = (action: string) => ACTIONS[action]?.[1] ?? action.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
export const actorRole = (actor: string) => ['board', 'dispatch', 'detector', 'system'].includes(actor) ? actor as 'board' | 'dispatch' | 'detector' | 'system' : 'person';
export const shortHash = (hash: string) => `${hash.slice(0, 12)}…`;
export function auditChips(data: AuditEntry['data']): { label: string; value: number | boolean }[] {
  return [
    ['Expected cost', data.expected_cost ?? data.expectedCost], ['Board cost', data.board_cost ?? data.boardCost],
    ['Cost delta', data.cost_delta ?? data.costDelta], ['Override', data.override],
  ].flatMap(([label, value]) => typeof value === 'number' || typeof value === 'boolean' ? [{ label: String(label), value }] : []);
}
export const cairoDateTime = (at: string) => new Date(at).toLocaleString('en-GB', { timeZone: 'Africa/Cairo', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
