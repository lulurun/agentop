/**
 * The features a service offers (docs/0_10_ui.md): Sessions from the core, whose
 * left panel also lists History, and one per enabled plugin that has a feature. Each has its own
 * layout: an optional left and right panel, and a main area of tabs.
 */
import type { PluginInfo } from "./api/client";
import { pluginsFor, UI_PLUGINS } from "./plugins/registry";
import type { TabContent } from "./tabs";

export type FeatureId = "sessions" | `plugin:${string}`;

export interface FeatureInfo {
  id: FeatureId;
  label: string;
  /** The plugin it belongs to, for a plugin's feature. */
  plugin?: string;
  left: boolean;
  right: boolean;
}

const CORE: FeatureInfo[] = [
  { id: "sessions", label: "Sessions", left: true, right: true },
];

export function featuresFor(plugins: PluginInfo[]): FeatureInfo[] {
  return [
    ...CORE,
    ...pluginsFor("feature", plugins).map(({ info, ui }) => ({
      id: `plugin:${info.id}` as const,
      label: ui.feature!.label,
      plugin: info.id,
      left: !!ui.feature!.left,
      right: !!ui.feature!.right,
    })),
  ];
}

export function pluginOf(id: FeatureId): string | undefined {
  return id.startsWith("plugin:") ? id.slice("plugin:".length) : undefined;
}

/** The project a feature always shows: Sessions is the `default` project. */
export const FIXED_PROJECT: Partial<Record<FeatureId, string>> = { sessions: "default" };

/** Its tabs belong to a project: Sessions', and a plugin feature's that says so. */
export function isProjectFeature(id: FeatureId): boolean {
  if (FIXED_PROJECT[id]) return true;
  const plugin = pluginOf(id);
  return !!plugin && !!UI_PLUGINS[plugin]?.feature?.projects;
}

/** A feature this app still offers: History was one until 2026-11-15, and is now in Sessions' left panel. */
export function isKnownFeature(id: string): id is FeatureId {
  return id === "sessions" || id.startsWith("plugin:");
}

/** A plugin tab whose view its plugin's UI no longer has, such as a pinned home tab saved before plugins had none. */
export function isStaleTab(content: TabContent): boolean {
  if (content.kind !== "plugin") return false;
  const feature = UI_PLUGINS[content.plugin]?.feature;
  return !!feature && !feature.views[content.view];
}
