import { request } from './client';
import type { WhyRecord } from './types';
import { isWhyRecord } from '../lib/why';

export async function fetchWhy(ref: string, signal?: AbortSignal): Promise<WhyRecord> {
  // Encode identifiers as one segment, but keep the reference separator accepted by {ref:path}.
  const path = encodeURIComponent(ref).replace(/%3A/gi, ':');
  const record = await request<WhyRecord>(`/why/${path}`, { signal });
  if (!isWhyRecord(record)) throw new Error('The server returned an invalid explanation.');
  return record;
}
