import { create } from 'zustand';
import { request } from '../api/client';
import type { AgentFinding, Alert, Incident, Reason } from '../api/types';

/** The language the agents speak, chosen by the person and kept by the server. It is not the language of the web app's own labels (the globe button). */
export type AgentLang = 'en' | 'ar';

interface AgentLangState { lang: AgentLang; loaded: boolean; load: () => Promise<void>; set: (lang: AgentLang) => Promise<void> }

export const useAgentLang = create<AgentLangState>()((setState, get) => ({
  lang: 'en',
  loaded: false,
  load: async () => {
    try { const s = await request<{ agentLang: AgentLang }>('/settings'); setState({ lang: s.agentLang, loaded: true }); } catch { setState({ loaded: true }); }
  },
  set: async (lang) => {
    const before = get().lang;
    setState({ lang });
    try { await request('/settings', { method: 'PUT', body: JSON.stringify({ agentLang: lang }) }); } catch { setState({ lang: before }); throw new Error('Could not change the language of the agents'); }
  },
}));

/** A finding with its Arabic text put in the fields the screens already read. Without an Arabic version (an older finding) the English one is shown as it is. */
export function localizeFinding<T extends AgentFinding | undefined>(f: T, lang: AgentLang): T {
  if (!f || lang !== 'ar' || !f.ar) return f;
  const ar = f.ar;
  return {
    ...f, headline: ar.headline, explanation: ar.explanation, probableCauses: ar.probableCauses, reasoningSteps: ar.reasoningSteps.length ? ar.reasoningSteps : f.reasoningSteps,
    predictedImpact: { ...f.predictedImpact, text: ar.impact }, recommendedAction: { ...f.recommendedAction, text: ar.action },
    explain: { ...f.explain, reasons: localizeReasons(f.explain.reasons, lang) },
  };
}
export const localizeReasons = (reasons: Reason[], lang: AgentLang): Reason[] => (lang === 'ar' ? reasons.map((r) => (r.textAr ? { ...r, text: r.textAr } : r)) : reasons);
export const incidentTitle = (i: Pick<Incident, 'title' | 'titleAr'>, lang: AgentLang) => (lang === 'ar' && i.titleAr ? i.titleAr : i.title);
export const alertTitle = (a: Pick<Alert, 'title' | 'titleAr'>, lang: AgentLang) => (lang === 'ar' && a.titleAr ? a.titleAr : a.title);
