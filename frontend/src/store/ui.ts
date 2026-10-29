import { create } from 'zustand';

interface Ui {
  scenarioOpen: boolean; chatOpen: boolean; assumptionsOpen: boolean; paletteOpen: boolean;
  selectedAsset: string | null; calcId: string | null; initialScenario: string | null;
  set: (patch: Partial<Omit<Ui, 'set'>>) => void;
}
export const useUi = create<Ui>()((set) => ({
  scenarioOpen: false, chatOpen: false, assumptionsOpen: false, paletteOpen: false,
  selectedAsset: null, calcId: null, initialScenario: null,
  set: (patch) => set(patch),
}));
