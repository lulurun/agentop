/**
 * The workspaces a service offers (design 10.7): Sessions from the core, whose
 * left panel also lists History, and one per enabled plugin that has a workspace. Each has its own
 * layout: an optional left and right panel, and a main area of tabs.
 */
import type { PluginInfo } from "./api/client";
import { pluginsFor, UI_PLUGINS } from "./plugins/registry";
import type { TabContent } from "./tabs";

export type WorkspaceId = "sessions" | `plugin:${string}`;

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

/** A workspace this app still offers: History was one until 2026-11-15, and is now in Sessions' left panel. */
export function isKnownWorkspace(id: string): id is WorkspaceId {
  return id === "sessions" || id.startsWith("plugin:");
}

/** A plugin tab whose view its plugin's UI no longer has, such as a pinned home tab saved before plugins had none. */
export function isStaleTab(content: TabContent): boolean {
  if (content.kind !== "plugin") return false;
  const workspace = UI_PLUGINS[content.plugin]?.workspace;
  return !!workspace && !workspace.views[content.view];
}
