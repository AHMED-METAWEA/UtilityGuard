import type { BoardDecision, Review } from '../api/types';

export function planHourNow(review: Pick<Review, 'at' | 'secondsPerHour'> | null | undefined, now: number, planLength: number): number | null {
  const start = Date.parse(review?.at ?? ''), seconds = review?.secondsPerHour;
  if (!Number.isFinite(start) || !seconds || !Number.isFinite(seconds) || seconds <= 0 || !Number.isFinite(now) || !Number.isFinite(planLength)) return null;
  return Math.min(Math.max(0, planLength), Math.max(0, (now - start) / 1000 / seconds));
}

export function moveOrder(order: string[], index: number, direction: -1 | 1): string[] {
  const target = index + direction;
  if (index < 0 || index >= order.length || target < 0 || target >= order.length) return order;
  const next = [...order];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export const proposalOrder = (decision: BoardDecision) => [...new Set([
  ...[...decision.plan.steps].sort((a, b) => a.order - b.order).map((step) => step.taskId),
  ...decision.tasks.map((task) => task.id),
])];

export function reviewError(error: unknown): string {
  const code = typeof error === 'object' && error !== null && 'code' in error ? String(error.code) : '';
  if (code === 'superseded' || code === 'already_superseded') return 'A newer proposal replaced this one. Open the latest proposal before deciding.';
  if (code === 'nothing_to_do' || code === 'already_stale') return 'Everything this proposal covered has already cleared. There is no work to dispatch.';
  if (code.startsWith('already_')) return 'This proposal has already been reviewed. Its latest status will arrive live.';
  return error instanceof Error ? error.message : 'The request could not be completed. Please try again.';
}

/** Execution owns the approved schedule; cost figures remain the proposal's figures. */
export function scheduledSteps(decision: BoardDecision) {
  return decision.plan.steps.map((step) => {
    const live = decision.execution?.find((item) => item.taskId === step.taskId);
    return live ? { ...step, crewSlot: live.crewSlot, startHour: live.planStartHour, endHour: live.planEndHour, dropped: live.crewSlot === 0 } : step;
  });
}

export const canApplyReview = (original: BoardDecision, current: BoardDecision | null | undefined, revision: number, currentRevision: number) =>
  revision === currentRevision && !!current && (current === original || (current.status ?? 'proposed') === 'proposed');
