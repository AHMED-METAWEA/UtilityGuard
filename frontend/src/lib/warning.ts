import type { EarlyWarning } from '../api/types';
import { fmt } from './format';

export function warningTitle(warning: EarlyWarning): string {
  if (warning.state === 'cleared') return 'Early warning cleared';
  if (warning.state === 'failed') return warning.leadTimeSeconds == null
    ? `${warning.assetName} failed after the warning`
    : `${warning.assetName} failed ${fmt(warning.leadTimeSeconds / 60, 1)} min after the warning`;
  return `${warning.assetName} is wearing out`;
}
