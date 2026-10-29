import { create } from 'zustand';

interface Ui {
  scenarioOpen: boolean; chatOpen: boolean; assumptionsOpen: boolean; paletteOpen: boolean;
  selectedAsset: string | null; calcId: string | null; initialScenario: string | null; toast: { id: number; text: string } | null;
  set: (patch: Partial<Omit<Ui, 'set' | 'notify'>>) => void;
  notify: (text: string) => void;
}
let toastTimer: ReturnType<typeof setTimeout> | undefined;
export const useUi = create<Ui>()((set) => ({
  scenarioOpen: false, chatOpen: false, assumptionsOpen: false, paletteOpen: false,
  selectedAsset: null, calcId: null, initialScenario: null, toast: null,
  set: (patch) => set(patch),
  notify: (text) => { clearTimeout(toastTimer); set({ toast: { id: Date.now(), text } }); toastTimer = setTimeout(() => set({ toast: null }), 3600); },
}));
