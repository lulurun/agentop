import type { ComponentType } from "react";
import type { ClientToken, PluginInfo, Session } from "../api/client";
import type { FileRef } from "../tabs";

/** The UI slots a plugin can fill. They match `ui` in a plugin's plugin.toml. */
export type Slot = "session-badge" | "workspace" | "right-panel" | "settings-section" | "file-viewer" | "login";

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
 * What a login plugin's component is given, on the sign-in screen. There is no
 * token yet, so its client reaches only the plugin's public routes.
 */
export interface LoginContext {
  id: string;
  /** The plugin's name, as the service lists it. */
  name: string;
  /** The service's name for this app, to sign in as. */
  clientName: string;
  client: PluginClient;
  /** Hand over the token the plugin's route returned (`api.sign_in`). The app saves it and connects. */
  signIn(token: ClientToken): void;
}

/** A view a plugin's tab shows. `viewKey` tells apart tabs of the same view, such as one per dialogue. */
export interface PluginView {
  label: string;
  /** The label of one of its tabs, from the tab's `viewKey`. Without it the tab shows `label: viewKey`. */
  tabLabel?(viewKey: string): string;
  Component: ComponentType<{ plugin: PluginContext; viewKey?: string }>;
}

/**
 * A plugin's workspace in the header (design 10.7). Its main area is tabs, like
 * every workspace's. With no tabs open it shows `empty`, as Sessions shows its placard.
 */
export interface PluginWorkspace {
  label: string;
  views: Record<string, PluginView>;
  /** The main area with no tabs open. */
  empty?: ComponentType<{ plugin: PluginContext }>;
  left?: ComponentType<{ plugin: PluginContext }>;
  right?: ComponentType<{ plugin: PluginContext }>;
  /** A button in the header, such as New dialogue. */
  action?: { label: string; run(plugin: PluginContext): void };
}

/**
 * A plugin's UI, in `ui/src/plugins/{id}/index.tsx` as `export const plugin`.
 * Each piece shows only when the service lists the plugin as enabled and its
 * manifest names that slot.
 */
export interface UiPlugin {
  sessionBadge?: ComponentType<{ session: Session; plugin: PluginContext }>;
  workspace?: PluginWorkspace;
  rightPanel?: ComponentType<{ session: Session; plugin: PluginContext }>;
  settingsSection?: ComponentType<{ plugin: PluginContext }>;
  /**
   * Shows a file in a main-area tab. While a plugin with one is enabled, the
   * files listed in the right panel open in a tab when clicked.
   */
  fileViewer?: ComponentType<{ file: FileRef; plugin: PluginContext }>;
  /**
   * A way to sign in, on the sign-in screen beside the service token. It shows
   * while the service lists the plugin in `GET /auth/methods`.
   */
  login?: ComponentType<{ login: LoginContext }>;
  /**
   * Asked before Settings turns the plugin off. A message warns the user, who
   * then confirms; null turns it off at once. A failure counts as null.
   */
  beforeDisable?(plugin: PluginContext): Promise<string | null>;
}

export const SLOT_KEYS: Record<Slot, keyof UiPlugin> = {
  "session-badge": "sessionBadge",
  workspace: "workspace",
  "right-panel": "rightPanel",
  "settings-section": "settingsSection",
  "file-viewer": "fileViewer",
  login: "login",
};
