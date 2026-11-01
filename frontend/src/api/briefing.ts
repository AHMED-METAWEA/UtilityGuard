import { request } from './client';
import { useFetch } from './dash';
import type { Briefing, BriefingChannel, BriefingDelivery, BriefingSendResult } from './types';
import { isBriefing, isBriefingChannel, isBriefingDelivery } from '../lib/briefing';

export function useBriefing(lang: Briefing['lang']) {
  const result = useFetch<unknown>(`/briefing?lang=${lang}`, 60000);
  const valid = isBriefing(result.data) && result.data.lang === lang;
  return { ...result, data: valid ? result.data as Briefing : null, error: !valid && !result.loading && !result.error ? 'Invalid briefing data.' : result.error };
}

export function useBriefingChannels() {
  const result = useFetch<unknown>('/briefing/channels', 60000);
  const valid = Array.isArray(result.data) && result.data.every(isBriefingChannel);
  return { ...result, data: valid ? result.data as BriefingChannel[] : null, error: !valid && !result.loading && !result.error ? 'Invalid briefing channel data.' : result.error };
}

export async function sendBriefing(lang: Briefing['lang'], channel: BriefingDelivery['channel'], signal?: AbortSignal): Promise<BriefingSendResult> {
  const data = await request<BriefingSendResult>('/briefing/send', { method: 'POST', body: JSON.stringify({ lang, channel }), signal });
  if (!data || !isBriefingDelivery(data.delivery) || !isBriefing(data.briefing) || data.briefing.lang !== lang || data.delivery.channel !== channel) throw new Error('Invalid briefing send result.');
  return data;
}
