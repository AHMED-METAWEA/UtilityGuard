import type { BoardStep } from '../api/types';

/** Fractions of a zero-based hour axis, independent of chart size and surface gaps. */
export function ganttPosition(startHour: number, endHour: number, maxHour: number) {
  const span = maxHour > 0 ? maxHour : 1;
  return { start: startHour / span, end: endHour / span, width: (endHour - startHour) / span };
}

/** Dropped problems occupy no crew lane and do not extend the work timeline. */
export function ganttLayout(steps: BoardStep[], crews: number) {
  const scheduled = steps.filter((step) => !step.dropped);
  const maxHour = Math.max(0, ...scheduled.map((step) => step.endHour)) || 1;
  const interval = maxHour > 12 ? 2 : 1;
  const ticks = Array.from({ length: Math.floor(maxHour / interval) + 1 }, (_, i) => i * interval);
  return {
    maxHour, ticks, laneCount: crews,
    responseHour: scheduled.length ? Math.min(...scheduled.map((step) => step.startHour)) : null,
    bars: scheduled.map((step) => ({ step, lane: step.crewSlot - 1, ...ganttPosition(step.startHour, step.endHour, maxHour) })),
    dropped: steps.filter((step) => step.dropped),
  };
}
