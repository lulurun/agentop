import type { PluginInfo } from "../api/client";
import { SLOT_KEYS, type Slot, type UiPlugin } from "./types";

/**
 * The plugin UIs built into this app, by plugin id: every `./{id}/index.tsx`
 * that exports `plugin`. A service never sends UI code (design 12.6).
 */
const found = import.meta.glob<{ plugin?: UiPlugin }>("./*/index.tsx", { eager: true });

export const UI_PLUGINS: Record<string, UiPlugin> = Object.fromEntries(
  Object.entries(found)
    .filter(([, module]) => module.plugin)
    .map(([path, module]) => [path.split("/")[1], module.plugin!]),
);

/** The plugins whose UI shows in a slot: enabled on the service, known to this app, and named in the manifest. */
export function pluginsFor(
  slot: Slot,
  infos: PluginInfo[],
  modules: Record<string, UiPlugin> = UI_PLUGINS,
): { info: PluginInfo; ui: UiPlugin }[] {
  return infos
    .filter((info) => info.state === "enabled" && info.ui.includes(slot) && modules[info.id]?.[SLOT_KEYS[slot]])
    .map((info) => ({ info, ui: modules[info.id] }));
}

/** The service has the plugin and it has screens, but this app version doesn't. */
export function missingFromApp(info: PluginInfo, modules: Record<string, UiPlugin> = UI_PLUGINS): boolean {
  return info.ui.length > 0 && !modules[info.id];
}
