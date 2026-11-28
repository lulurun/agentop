import type { ComponentType } from "react";
import type { ClientToken, PluginInfo, Session } from "../api/client";
import type { FileRef } from "../tabs";

/** The UI slots a plugin can fill. They match `ui` in a plugin's plugin.toml. */
export type Slot = "session-badge" | "feature" | "right-panel" | "settings-section" | "file-viewer" | "login" | "terminal-input";

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
  /** The service's base URL, to tell services apart in anything the plugin keeps. */
  url: string;
  /** The service's name for this app, to sign in as. */
  clientName: string;
  client: PluginClient;
  /** Hand over the token the plugin's route returned (`api.sign_in`). The app saves it and connects. */
  signIn(token: ClientToken): void;
}

/**
 * What a `terminal-input` piece is given to say it is taking the input. While
 * any piece takes it, the terminal above is only watched: what is typed there is
 * dropped, but it still takes the tab's size and scrolls. A piece that crashes
 * or goes away gives the input back.
 */
export interface TerminalInputControl {
  /** `focus` is called when the watched terminal is clicked, to move the keyboard to the piece. */
  takeInput(on: boolean, focus?: () => void): void;
  /**
   * Type into the terminal as its keyboard would, through tmux's own client, so
   * tmux's key bindings apply (its prefix, then a key). It works while the input
   * is taken, and returns false when the terminal isn't connected.
   */
  type(data: string): boolean;
}

/** A view a plugin's tab shows. `viewKey` tells apart tabs of the same view, such as one per dialogue. */
export interface PluginView {
  label: string;
  /** The label of one of its tabs, from the tab's `viewKey`. Without it the tab shows `label: viewKey`. */
  tabLabel?(viewKey: string): string;
  Component: ComponentType<{ plugin: PluginContext; viewKey?: string }>;
  /** The session whose terminal the tab shows instead of `Component` while there is one, as a project's Coordinator. */
  session?(viewKey: string, sessions: Record<string, Session>): string | undefined;
}

/**
 * A plugin's feature (docs/0_10_ui.md): a button in the feature nav, its left and
 * right panels (made of `Section`s), its background, and the views its tabs show.
 */
export interface PluginFeature {
  label: string;
  views: Record<string, PluginView>;
  /** The view of its default tab, pinned first in the left area as Sessions' start buffer is. */
  home?: string;
  /**
   * Its tabs and panels are per project, as Projects' and Dialogues' are: the core keeps one
   * layout for each, in the project's `layout.json`, and shows the one picked with `setProject`.
   */
  projects?: boolean;
  /** Views each of its projects always has, one tab per area keyed by the project's name, that can't be closed or moved. */
  fixed?: Partial<Record<"left" | "right", string>>;
  /** The middle panel with no areas. */
  background?: ComponentType<{ plugin: PluginContext }>;
  left?: ComponentType<{ plugin: PluginContext }>;
  right?: ComponentType<{ plugin: PluginContext }>;
}

/**
 * A plugin's UI, in `ui/src/plugins/{id}/index.tsx` as `export const plugin`.
 * Each piece shows only when the service lists the plugin as enabled and its
 * manifest names that slot.
 */
export interface UiPlugin {
  sessionBadge?: ComponentType<{ session: Session; plugin: PluginContext }>;
  feature?: PluginFeature;
  /** A section in Sessions' right panel, titled with the plugin's name. */
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
  /** Drawn below a session's terminal, such as a box to type prompts in. */
  terminalInput?: ComponentType<{ session: Session; plugin: PluginContext; terminal: TerminalInputControl }>;
  /**
   * Asked before Settings turns the plugin off. A message warns the user, who
   * then confirms; null turns it off at once. A failure counts as null.
   */
  beforeDisable?(plugin: PluginContext): Promise<string | null>;
}

export const SLOT_KEYS: Record<Slot, keyof UiPlugin> = {
  "session-badge": "sessionBadge",
  feature: "feature",
  "right-panel": "rightPanel",
  "settings-section": "settingsSection",
  "file-viewer": "fileViewer",
  login: "login",
  "terminal-input": "terminalInput",
};
