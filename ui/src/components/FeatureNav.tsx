import { featureKey } from "../hotkeys";
import { currentFeature, useApp } from "../store";
import { featuresFor, type FeatureInfo } from "../features";

export function useFeatures(): { list: FeatureInfo[]; current: FeatureInfo } {
  const plugins = useApp((s) => s.plugins);
  const id = useApp(currentFeature);
  const list = featuresFor(plugins);
  return { list, current: list.find((w) => w.id === id) ?? list[0] };
}

/** The selected service's features. The current one is highlighted and can't be clicked. */
export function FeatureNav() {
  const { list, current } = useFeatures();
  const setFeature = useApp((s) => s.setFeature);
  return (
    <>
      <nav className="features" aria-label="Features">
        {list.map((w, index) => {
          const here = w.id === current.id;
          return (
            <button
              key={w.id}
              type="button"
              aria-current={here ? "page" : undefined}
              aria-disabled={here || undefined}
              title={featureKey(index) ? `${w.label} (${featureKey(index)})` : w.label}
              onClick={() => !here && setFeature(w.id)}
            >
              {w.label}
            </button>
          );
        })}
      </nav>
      <select className="features-compact" aria-label="Feature" value={current.id} onChange={(event) => setFeature(event.target.value as FeatureInfo["id"])}>
        {list.map((w) => (
          <option key={w.id} value={w.id}>
            {w.label}
          </option>
        ))}
      </select>
    </>
  );
}
