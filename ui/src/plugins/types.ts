import type { ComponentType } from "react";
import type { PluginInfo, Session } from "../api/client";
import type { FileRef } from "../tabs";

/** The UI slots a plugin can fill. They match `ui` in a plugin's plugin.toml. */
export type Slot = "session-badge" | "left-view" | "right-panel" | "settings-section" | "file-viewer";

/** Requests to the plugin's own routes, `/api/v2/plugins/{id}/…`. It can reach nothing else. */
export interface PluginClient {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body?: unknown): Promise<T>;
  delete<T>(path: string): Promise<T>;
}

/** What a plugin's components are given. */
export interface PluginContext {
  id: string;
  info: PluginInfo;
  client: PluginClient;
  /** Listen for the plugin's `api.emit(data)`. Returns a function that stops listening. */
  onEvent(listener: (data: unknown) => void): () => void;
}

/**
 * A plugin's UI, in `ui/src/plugins/{id}/index.tsx` as `export const plugin`.
 * Each piece shows only when the service lists the plugin as enabled and its
 * manifest names that slot.
 */
export interface UiPlugin {
  sessionBadge?: ComponentType<{ session: Session; plugin: PluginContext }>;
  leftView?: { label: string; Component: ComponentType<{ plugin: PluginContext }> };
  rightPanel?: ComponentType<{ session: Session; plugin: PluginContext }>;
  settingsSection?: ComponentType<{ plugin: PluginContext }>;
  /**
   * Shows a file in a main-area tab. While a plugin with one is enabled, the
   * files listed in the right panel open in a tab when clicked.
   */
  fileViewer?: ComponentType<{ file: FileRef; plugin: PluginContext }>;
  /**
   * Asked before Settings turns the plugin off. A message warns the user, who
   * then confirms; null turns it off at once. A failure counts as null.
   */
  beforeDisable?(plugin: PluginContext): Promise<string | null>;
}

export const SLOT_KEYS: Record<Slot, keyof UiPlugin> = {
  "session-badge": "sessionBadge",
  "left-view": "leftView",
  "right-panel": "rightPanel",
  "settings-section": "settingsSection",
  "file-viewer": "fileViewer",
};
