/**
 * The workspaces a service offers (design 10.7): Sessions and History from the
 * core, and one per enabled plugin that has a workspace. Each has its own
 * layout: an optional left and right panel, and a main area of tabs.
 */
import type { PluginInfo } from "./api/client";
import { pluginsFor, UI_PLUGINS } from "./plugins/registry";
import type { TabContent } from "./tabs";

export type WorkspaceId = "sessions" | "history" | `plugin:${string}`;

export interface WorkspaceInfo {
  id: WorkspaceId;
  label: string;
  /** The plugin it belongs to, for a plugin's workspace. */
  plugin?: string;
  left: boolean;
  right: boolean;
}

const CORE: WorkspaceInfo[] = [
  { id: "sessions", label: "Sessions", left: true, right: true },
  { id: "history", label: "History", left: false, right: true },
];

export function workspacesFor(plugins: PluginInfo[]): WorkspaceInfo[] {
  return [
    ...CORE,
    ...pluginsFor("workspace", plugins).map(({ info, ui }) => ({
      id: `plugin:${info.id}` as const,
      label: ui.workspace!.label,
      plugin: info.id,
      left: !!ui.workspace!.left,
      right: !!ui.workspace!.right,
    })),
  ];
}

export function pluginOf(id: WorkspaceId): string | undefined {
  return id.startsWith("plugin:") ? id.slice("plugin:".length) : undefined;
}

/** The pinned tab a workspace opens with. Sessions has none: it opens empty, or with the session selected last. */
export function homeOf(id: WorkspaceId): TabContent | undefined {
  if (id === "history") return { kind: "history" };
  const plugin = pluginOf(id);
  const home = plugin ? UI_PLUGINS[plugin]?.workspace?.home : undefined;
  return plugin && home ? { kind: "plugin", plugin, view: home } : undefined;
}
