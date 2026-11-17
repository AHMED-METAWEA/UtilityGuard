import type { ForecastEvidence } from '../api/dash';

type DayType = NonNullable<ForecastEvidence['byDayType']>[number];
type Label = NonNullable<ForecastEvidence['reliability']>['label'];

const VERDICT: Record<Label, { cls: string; label: string }> = {
  reliable: { cls: 'ok', label: 'Reliable' },
  indicative: { cls: 'warn', label: 'Indicative' },
  unreliable: { cls: 'crit', label: 'Unreliable' },
};

export const reliabilityChip = (label: Label) => VERDICT[label];
export const sortDayTypes = (rows: readonly DayType[]): DayType[] => [...rows].sort((a, b) => b.mape - a.mape);
export const worstDayType = (rows: readonly DayType[]): DayType | undefined => sortDayTypes(rows)[0];
