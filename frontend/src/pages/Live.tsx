import { CityMap } from '../map/CityMap';
import { useUi } from '../store/ui';

export function Live() {
  const sel = useUi((s) => s.selectedAsset);
  const set = useUi((s) => s.set);
  return (
    <div className="page">
      <CityMap height="calc(100vh - 56px - 48px)" selectedAsset={sel} onSelectAsset={(id) => set({ selectedAsset: id })} />
    </div>
  );
}
