import { useMemo } from "react";
import { create } from "zustand";
import { ApiError, ServiceClient, type ClientToken, type HistoryItem, type PluginInfo, type Session, type ServiceInfo } from "./api/client";
import {
  checkConnection,
  mergeCredentials,
  credentialsFrom,
  EXPIRED,
  needsRenewal,
  RENEW_CHECK_MS,
  SIGNED_OUT,
  type ConnectFailure,
  type Connection,
} from "./connections";
import { platform } from "./platform";
import { applyTheme, loadSettings, saveSettings, type Settings } from "./settings";
import * as tabs from "./tabs";
import type { Area, Layout, OpenMode, Tab, TabContent } from "./tabs";
import { UI_PLUGINS } from "./plugins/registry";
import { FIXED_PROJECT, isKnownFeature, isProjectFeature, isStaleTab, pluginOf, featuresFor, type FeatureId } from "./features";
import { defaultView, fromFile, LayoutSync, sameView, toFile, withoutSizes, type ProjectView } from "./layoutfile";
import { clampSide, clampSplit, readSide, readSplit } from "./sizes";

const DEFAULT_PROJECT = "default";

/** A project's view, and whether its file has been read yet (or failed to be) since the page opened. */
export interface ProjectEntry extends ProjectView {
  ready: boolean;
}

export type LinkState =
  | { state: "connecting" }
  | { state: "connected"; service: ServiceInfo }
  | { state: "reconnecting"; service: ServiceInfo; socketBlocked?: boolean }
  | { state: "failed"; failure: ConnectFailure };

type Dialog = "add-service" | "settings" | null;

/** History, in Sessions' left panel: the past sessions agentop started, for the active connection. */
export interface HistoryState {
  /** Newest first. Null until loaded. */
  items: HistoryItem[] | null;
  /** How recently a session must have been used to be listed. */
  windowSeconds: number | null;
  /** How many sessions may be active at once. */
  maxActive: number;
}

const NO_HISTORY: HistoryState = { items: null, windowSeconds: null, maxActive: 20 };

/** The sessions that count towards the limit: the ones agentop started, unless their plugin says otherwise. */
/** Sessions counted against the service's limit, all of them or one project's. */
export function activeCount(sessions: Record<string, Session>, project?: string): number {
  return Object.values(sessions).filter(
    (s) => s.managed && s.counted !== false && (project === undefined || (s.project ?? "default") === project),
  ).length;
}

export interface AppState {
  connections: Connection[];
  activeId: string;
  /** The feature shown, per connection. */
  features: Record<string, FeatureId>;
  settings: Settings;
  link: LinkState;
  sessions: Record<string, Session>;
  /** The connected service's plugins. Plugins differ by service, so this is reset on every switch. */
  plugins: PluginInfo[];
  /** The plugin list has come in since connecting, so a missing plugin feature is really missing. */
  pluginsLoaded: boolean;
  history: HistoryState;
  /** Selected session per connection: the focused tab's session. Session IDs are only unique within one service. */
  selected: Record<string, string>;
  /** The main area's tabs per connection, one layout per feature that isn't a project's, as Memory's. */
  layouts: Record<string, Partial<Record<FeatureId, Layout>>>;
  /** Per connection, the project each project feature shows; Sessions always shows `default`. */
  scopes: Record<string, Partial<Record<FeatureId, string>>>;
  /** Per connection and project: its tabs and panels, from its layout.json (docs/0_10_ui.md, Project layouts). */
  projectViews: Record<string, Record<string, ProjectEntry>>;
  /** The sessions' snapshot has come in since connecting, so a session missing from it is gone. */
  sessionsLoaded: boolean;
  leftOpen: boolean;
  rightOpen: boolean;
  /** This browser's side panel widths, for a feature without a project; undefined for the default. */
  leftWidth?: number;
  rightWidth?: number;
  /** The middle panel's split per connection, for each feature whose layout isn't a project's. */
  splits: Record<string, Partial<Record<FeatureId, number>>>;
  /** The panels' sections folded shut, by `Section` id. */
  folded: string[];
  switcherOpen: boolean;
  dialog: Dialog;
  /** The project New session starts its session in. */
  error: string | null;

  init(): Promise<void>;
  switchTo(id: string): void;
  retry(): void;
  /** Sign in with the service token. */
  setToken(token: string): void;
  /** Take a client token another way of signing in got (a login plugin), and connect with it. */
  signIn(token: ClientToken): void;
  /** Sign this app out of the active service, and revoke its client there. */
  signOut(): Promise<void>;
  setFeature(feature: FeatureId): void;
  /** Show a project in a project feature, its layout read again from its file; null for none. */
  setProject(feature: FeatureId, project: string | null): Promise<void>;
  setPluginEnabled(id: string, enabled: boolean): Promise<void>;
  updateSettings(changes: Partial<Settings>): void;
  /** The service was stopped on purpose, so don't keep trying to reach it. */
  markStopped(): void;
  addConnection(connection: Connection): void;
  updateConnection(id: string, fields: Partial<Connection>): void;
  removeConnection(id: string): void;
  /** Show a session in Sessions, by default in the left area in place of the session shown. */
  select(sessionId: string, mode?: OpenMode, area?: Area): void;
  /** Open a tab in an area (docs/0_10_ui.md, Where tabs open) of the feature shown; a default-project session's tab always opens in Sessions. */
  openTab(content: TabContent, area: Area, mode?: OpenMode): void;
  /** Open a tab in one captured view (`ownerOf`), shown or not, for an action that awaited first; it never switches what is shown. */
  openTabIn(owner: ViewOwner, content: TabContent, area: Area, mode?: OpenMode): void;
  focusAreaIn(owner: ViewOwner, area: Area): void;
  closeTabsIn(owner: ViewOwner, matches: (content: TabContent) => boolean): void;
  /** Close the tabs of a feature whose content matches. */
  closeTabs(feature: FeatureId, matches: (content: TabContent) => boolean): void;
  activateTab(feature: FeatureId, area: Area, tabId: string): void;
  closeTab(feature: FeatureId, area: Area, tabId: string): void;
  moveTab(feature: FeatureId, area: Area, tabId: string): void;
  focusArea(feature: FeatureId, area: Area): void;
  /** The feature shown: pick the active area's tab at a position. */
  activateTabAt(index: number): void;
  /** The feature shown: close the active area's tab. */
  closeActiveTab(): void;
  loadHistory(): Promise<void>;
  /** Resume a past session; its tab becomes the new session's. */
  resumeHistory(item: HistoryItem): Promise<void>;
  /** Delete a past session's transcript from its tool, and close its tab. */
  removeHistory(item: HistoryItem): Promise<void>;
  toggleLeft(): void;
  toggleRight(): void;
  /** Shut or open an area of the shown feature's middle panel, keeping its tabs. */
  toggleArea(area: Area): void;
  /** The shown view's side panel width, in pixels. */
  setPanelWidth(side: "left" | "right", width: number): void;
  /** A feature's split: the left area's share of its middle panel. */
  setSplit(feature: FeatureId, share: number): void;
  setFolded(section: string, folded: boolean): void;
  setSwitcherOpen(open: boolean): void;
  setDialog(dialog: Dialog, project?: string): void;
  /** Open a service's Status tab in the feature shown, switching to that service first. */
  openStatus(id: string): void;
  setError(error: string | null): void;
  /** Run an action against the service, showing any failure instead of throwing. */
  act<T>(action: (client: ServiceClient) => Promise<T>): Promise<T | undefined>;
}

const LAYOUT_KEY = "agentop.layout";
const SPLITS_KEY = "agentop.splits";
const FOLDED_KEY = "agentop.folded";

function loadFolded(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(FOLDED_KEY) ?? "[]");
    return Array.isArray(saved) ? saved.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}
const SELECTED_KEY = "agentop.selected";
const TABS_KEY = "agentop.tabs";
const FEATURE_KEY = "agentop.workspace";
const SCOPES_KEY = "agentop.scopes";
/** Where Projects kept its chosen project before the core did. */
const OLD_PROJECTS_KEY = "agentop.projects.current";

/** The chosen project per connection and feature; Projects' from before the core kept it too. */
export function loadScopes(storage: Pick<Storage, "getItem"> = localStorage): AppState["scopes"] {
  try {
    const saved = JSON.parse(storage.getItem(SCOPES_KEY) ?? "null");
    if (saved && typeof saved === "object") return saved;
    const old = JSON.parse(storage.getItem(OLD_PROJECTS_KEY) ?? "{}");
    const scopes: AppState["scopes"] = {};
    for (const [connection, name] of Object.entries(old ?? {})) {
      if (typeof name === "string") scopes[connection] = { "plugin:projects": name };
    }
    return scopes;
  } catch {
    return {};
  }
}

type Layouts = AppState["layouts"];

/**
 * Saved tabs, per connection and feature. Tabs saved before features were
 * one layout per connection: those become the Sessions layout. Sessions' are read only
 * to move into the default project's layout.json; another project feature's held every
 * project's tabs at once, so they are dropped rather than given to any one project.
 */
export function parseLayouts(saved: Record<string, unknown>): Layouts {
  const result: Layouts = {};
  for (const [id, value] of Object.entries(saved ?? {})) {
    if (!value || typeof value !== "object") continue;
    if (Array.isArray((value as { groups?: unknown }).groups)) {
      const layout = tabs.parseLayout(value);
      if (layout) result[id] = { sessions: layout };
      continue;
    }
    const byFeature: Partial<Record<FeatureId, Layout>> = {};
    for (const [feature, layout] of Object.entries(value as Record<string, unknown>)) {
      if (!isKnownFeature(feature) || (feature !== "sessions" && isProjectFeature(feature))) continue;
      const parsed = tabs.parseLayout(layout);
      if (parsed) byFeature[feature as FeatureId] = tabs.closeWhere(parsed, isStaleTab);
    }
    result[id] = byFeature;
  }
  return result;
}

/** A project feature's tabs while it shows no project, as Status or the New dialogue wizard; kept apart from the old mixed ones. */
const NO_PROJECT_KEY = "agentop.tabs.noproject";

function loadLayouts(): Layouts {
  const read = (key: string) => {
    try {
      return JSON.parse(localStorage.getItem(key) ?? "{}");
    } catch {
      return {};
    }
  };
  const layouts = parseLayouts(read(TABS_KEY));
  for (const [id, byFeature] of Object.entries(read(NO_PROJECT_KEY) as Record<string, Record<string, unknown>>)) {
    for (const [feature, value] of Object.entries(byFeature ?? {})) {
      const layout = isKnownFeature(feature) && isProjectFeature(feature) && feature !== "sessions" ? tabs.parseLayout(value) : null;
      if (layout) layouts[id] = { ...layouts[id], [feature]: tabs.closeWhere(layout, isStaleTab) };
    }
  }
  return layouts;
}

function loadFeatures(): Record<string, FeatureId> {
  try {
    const saved = JSON.parse(localStorage.getItem(FEATURE_KEY) ?? "{}");
    return saved && typeof saved === "object" ? saved : {};
  } catch {
    return {};
  }
}

/**
 * The feature shown for the active connection. One whose plugin is off, or
 * isn't on this service, falls back to Sessions once the plugin list is in.
 */
export function currentFeature(state: Pick<AppState, "features" | "activeId" | "plugins" | "pluginsLoaded">): FeatureId {
  const saved = state.features[state.activeId];
  const wanted = saved && isKnownFeature(saved) ? saved : "sessions";
  if (!pluginOf(wanted) || !state.pluginsLoaded) return wanted;
  return featuresFor(state.plugins).some((w) => w.id === wanted) ? wanted : "sessions";
}

/** The project a feature shows on the active connection: `default` for Sessions, the one picked for another project feature. */
export function projectOf(state: Pick<AppState, "scopes" | "activeId">, feature: FeatureId): string | undefined {
  return FIXED_PROJECT[feature] ?? (isProjectFeature(feature) ? state.scopes[state.activeId]?.[feature] : undefined);
}

/**
 * Where `selected` keeps a feature's selected session: Sessions' under the connection's ID, as before projects;
 * another project feature's per project, so one project's right panel never shows another's session.
 */
export function selectionKey(activeId: string, feature: FeatureId, project?: string): string {
  if (feature === "sessions") return activeId;
  if (project) return `${activeId}|${feature}|${project}`;
  // A project feature with none shown has a key of its own, not the one it once shared across its projects.
  return isProjectFeature(feature) ? `${activeId}|${feature}|` : `${activeId}|${feature}`;
}

/** The session the feature shown has selected: the one its right panel shows. */
export function selectedId(state: Pick<AppState, "selected" | "activeId" | "features" | "plugins" | "pluginsLoaded" | "scopes">): string | undefined {
  const feature = currentFeature(state);
  return state.selected[selectionKey(state.activeId, feature, projectOf(state, feature))];
}

const narrow = () => typeof window !== "undefined" && window.innerWidth < 900;

/**
 * Whether the side panels are open: the shown project's, or this browser's own for a feature without one.
 * On a narrow screen they are overlays that start shut, so a project's file doesn't open them there.
 */
export function panelsOf(state: Pick<AppState, "scopes" | "activeId" | "projectViews" | "leftOpen" | "rightOpen" | "features" | "plugins" | "pluginsLoaded">): { leftOpen: boolean; rightOpen: boolean } {
  const project = projectOf(state, currentFeature(state));
  const view = project ? state.projectViews[state.activeId]?.[project] : undefined;
  return view?.ready && !narrow() ? view : state;
}

type PanelState = Pick<AppState, "scopes" | "activeId" | "projectViews" | "leftWidth" | "rightWidth" | "features" | "plugins" | "pluginsLoaded">;

/** The side panels' widths set for the view shown, the shown project's or this browser's; undefined for the default. */
export function widthsOf(state: PanelState): { leftWidth?: number; rightWidth?: number } {
  const project = projectOf(state, currentFeature(state));
  const view = project ? state.projectViews[state.activeId]?.[project] : undefined;
  return view?.ready ? view : state;
}

/** A feature's split on the active connection: its project's, or this browser's for a layout that isn't a project's. */
export function splitOf(state: Pick<AppState, "scopes" | "activeId" | "projectViews" | "splits">, feature: FeatureId): number | undefined {
  const project = projectOf(state, feature);
  if (project) return state.projectViews[state.activeId]?.[project]?.split;
  return state.splits[state.activeId]?.[feature];
}

/** One view: a feature on a connection, and the project it shows, if any. */
export interface ViewOwner {
  connection: string;
  feature: FeatureId;
  project?: string;
}

/** The view a feature shows now, by default the feature shown; captured before an await, so what follows lands there. */
export function ownerOf(state: Pick<AppState, "scopes" | "activeId" | "features" | "plugins" | "pluginsLoaded">, feature: FeatureId = currentFeature(state)): ViewOwner {
  return { connection: state.activeId, feature, project: projectOf(state, feature) };
}

/** That view is the one on screen. */
export function isShown(state: Pick<AppState, "scopes" | "activeId" | "features" | "plugins" | "pluginsLoaded">, owner: ViewOwner): boolean {
  return state.activeId === owner.connection && currentFeature(state) === owner.feature && projectOf(state, owner.feature) === owner.project;
}

/** A view's tabs, whether or not it is shown. */
export function layoutIn(state: Pick<AppState, "layouts" | "projectViews">, { connection, feature, project }: ViewOwner): Layout {
  const saved = project ? state.projectViews[connection]?.[project]?.layout : state.layouts[connection]?.[feature];
  return withFeaturePinned(saved ?? EMPTY_LAYOUT, feature);
}

/** The feature's layout has been read, or there is none to read: until then its middle panel shows nothing. */
export function layoutReady(state: Pick<AppState, "scopes" | "activeId" | "projectViews">, feature: FeatureId): boolean {
  const project = projectOf(state, feature);
  return !project || !!state.projectViews[state.activeId]?.[project]?.ready;
}

/**
 * The feature a project's session opens in: Sessions for the default project's,
 * and the feature shown for another project's, which only its own feature lists.
 */
export function featureForProject(state: Pick<AppState, "features" | "activeId" | "plugins" | "pluginsLoaded">, project?: string | null): FeatureId {
  if (!project || project === "default") return "sessions";
  return currentFeature(state);
}

const EMPTY_LAYOUT = tabs.emptyLayout();

type LayoutState = Pick<AppState, "layouts" | "selected" | "activeId" | "features" | "plugins" | "pluginsLoaded" | "scopes" | "projectViews">;

/** A layout with the feature's pinned tab in it: Sessions' start buffer, or a plugin's `home`. */
function withFeaturePinned(layout: Layout, feature: FeatureId): Layout {
  if (feature === "sessions") return withPinned(layout, tabs.START_TAB);
  const home = homeTab(feature);
  return home ? withPinned(layout, home) : layout;
}

/** A feature's tabs on the active connection, by default the one shown: those of the project it shows, if it shows one. */
export function layoutOf(state: LayoutState, feature: FeatureId = currentFeature(state)): Layout {
  return layoutIn(state, ownerOf(state, feature));
}

const homeTabs = new Map<string, Tab>();

/** A plugin feature's pinned tab, from its UI's `home` view; the same object each time. */
function homeTab(feature: FeatureId): Tab | undefined {
  const plugin = pluginOf(feature);
  const view = plugin && UI_PLUGINS[plugin]?.feature?.home;
  if (!plugin || !view) return undefined;
  const key = `${plugin}:${view}`;
  if (!homeTabs.has(key)) homeTabs.set(key, { id: "home", content: { kind: "plugin", plugin, view }, pinned: true });
  return homeTabs.get(key);
}

const pinnedAdded = new WeakMap<Layout, WeakMap<Tab, Layout>>();

/** A layout with its pinned tab, the same object each time for the same layout, as selectors need. */
function withPinned(layout: Layout, pinned: Tab): Layout {
  let byTab = pinnedAdded.get(layout);
  if (!byTab) pinnedAdded.set(layout, (byTab = new WeakMap()));
  let found = byTab.get(pinned);
  if (!found) byTab.set(pinned, (found = tabs.withPinnedTab(layout, pinned)));
  return found;
}

/** The past session whose tab has focus in the feature shown, if one does. */
export function focusedPastId(state: LayoutState): string | undefined {
  const content = tabs.focusedTab(layoutOf(state))?.content;
  return content?.kind === "past" ? content.historyId : undefined;
}

function loadSelected(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(SELECTED_KEY) ?? "{}");
  } catch {
    return {};
  }
}

/** The side panels as this browser saved them, whatever the screen's width. */
function savedPanels(): { leftOpen: boolean; rightOpen: boolean } | null {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null");
    return saved && typeof saved.leftOpen === "boolean" && typeof saved.rightOpen === "boolean" ? { leftOpen: saved.leftOpen, rightOpen: saved.rightOpen } : null;
  } catch {
    return null;
  }
}

interface BrowserPanels {
  leftOpen: boolean;
  rightOpen: boolean;
  leftWidth?: number;
  rightWidth?: number;
}

function loadLayout(): BrowserPanels {
  const open = savedPanels();
  let widths: { leftWidth?: number; rightWidth?: number } = {};
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null");
    widths = { leftWidth: readSide(saved?.leftWidth), rightWidth: readSide(saved?.rightWidth) };
  } catch {
    // The defaults.
  }
  return { ...(open && !narrow() ? open : { leftOpen: !narrow(), rightOpen: !narrow() }), ...widths };
}

function saveLayout({ leftOpen, rightOpen, leftWidth, rightWidth }: BrowserPanels) {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ leftOpen, rightOpen, leftWidth, rightWidth }));
  } catch {
    // Only a convenience.
  }
}

function loadSplits(): AppState["splits"] {
  try {
    const saved = JSON.parse(localStorage.getItem(SPLITS_KEY) ?? "{}");
    const splits: AppState["splits"] = {};
    for (const [connection, features] of Object.entries(saved && typeof saved === "object" ? saved : {})) {
      for (const [feature, share] of Object.entries(features && typeof features === "object" ? features : {})) {
        const split = readSplit(share);
        if (split !== undefined) splits[connection] = { ...splits[connection], [feature]: split };
      }
    }
    return splits;
  } catch {
    return {};
  }
}

/** The service token is never saved: it only signs in, and is dropped once it has. */
function persist(connections: Connection[]) {
  platform.saveConnections(
    connections.filter((c) => !c.isHome || c.token || c.clientId).map(({ serviceToken: _, ...saved }) => saved),
  );
}

type Event =
  | { type: "snapshot"; sessions: Session[] }
  | { type: "session.upserted"; session: Session }
  | { type: "session.removed"; id: string }
  | { type: "plugins" }
  | { type: "plugin"; id: string; data: unknown };

const pluginListeners = new Map<string, Set<(data: unknown) => void>>();

/** Listen for one plugin's events from the active service. Returns a function that stops listening. */
export function onPluginEvent(id: string, listener: (data: unknown) => void): () => void {
  if (!pluginListeners.has(id)) pluginListeners.set(id, new Set());
  pluginListeners.get(id)!.add(listener);
  return () => pluginListeners.get(id)?.delete(listener);
}

function dispatchPluginEvent(id: string, data: unknown) {
  for (const listener of pluginListeners.get(id) ?? []) {
    try {
      listener(data);
    } catch (error) {
      console.error(`Plugin ${id} event listener failed`, error);
    }
  }
}

/** Events that aren't about sessions, including ones from a newer service, leave the list as it is. */
export function applyEvent(sessions: Record<string, Session>, event: Event): Record<string, Session> {
  switch (event.type) {
    case "snapshot":
      return Object.fromEntries(event.sessions.map((s) => [s.id, s]));
    case "session.upserted":
      return { ...sessions, [event.session.id]: event.session };
    case "session.removed": {
      const { [event.id]: _removed, ...rest } = sessions;
      return rest;
    }
    default:
      return sessions;
  }
}

const idKeys = new WeakMap<Record<string, Session>, string>();

/** The sessions' ids, sorted and joined: it changes only when a session comes or goes. Computed once per change. */
export function sessionIdsKey(sessions: Record<string, Session>): string {
  let key = idKeys.get(sessions);
  if (key === undefined) {
    key = Object.keys(sessions).sort().join("\n");
    idKeys.set(sessions, key);
  }
  return key;
}

export function sortedSessions(sessions: Record<string, Session>): Session[] {
  return Object.values(sessions).sort(
    (a, b) => (b.created_at ?? 0) - (a.created_at ?? 0) || a.id.localeCompare(b.id),
  );
}

/** External agents have no name of their own, so their conversation title is the best label. */
export function sessionTitle(session: Session): string {
  return session.managed ? session.display_name : (session.ai_title ?? session.display_name);
}

/**
 * What the session is about: the user's description, or else the agent's own
 * summary of the conversation, unless that is already the title.
 */
export function sessionSummary(session: Session): string | null {
  const description = session.description.trim();
  if (description) return description;
  const summary = session.ai_title?.trim();
  return summary && summary !== sessionTitle(session) ? summary : null;
}

export function activeConnection(state: Pick<AppState, "connections" | "activeId">): Connection {
  return state.connections.find((c) => c.id === state.activeId)!;
}

export function activeClient(state: Pick<AppState, "connections" | "activeId">): ServiceClient {
  const connection = activeConnection(state);
  return new ServiceClient(connection.url, connection.token);
}

/**
 * The active service's client, kept while its address and sign-in stay the same. It reads the
 * token on each request, so a renewed token, or any other change to the connections, reloads nothing.
 */
export function useActiveClient(): ServiceClient {
  const id = useApp((s) => s.activeId);
  const url = useApp((s) => activeConnection(s)?.url ?? "");
  const signedIn = useApp((s) => !!activeConnection(s)?.token);
  return useMemo(
    () => new ServiceClient(url, () => useApp.getState().connections.find((c) => c.id === id)?.token ?? ""),
    // signedIn: signing in or out is a different client, so what it shows loads again.
    [id, url, signedIn],
  );
}

const RETRY_MS = { first: 500, max: 10_000 };
/** How long session events are gathered before they are applied in one store change. */
const EVENT_BATCH_MS = 30;
/** The service answered but its socket refused this many times: something between them is blocking it. */
const BLOCKED_AFTER = 3;

export const useApp = create<AppState>((set, get) => {
  // Each connect attempt gets a number, and anything from an older attempt is
  // ignored. Without this, a slow service could overwrite the one switched to.
  let attempt = 0;
  let socket: WebSocket | null = null;

  // Each connection's own client, never the active one: a save that is late must reach the service it was made for.
  function clientFor(connection: string): ServiceClient {
    const found = get().connections.find((c) => c.id === connection);
    if (!found) throw new Error(`No connection ${connection}`);
    return new ServiceClient(found.url, () => get().connections.find((c) => c.id === connection)?.token ?? "");
  }

  const sync = new LayoutSync({
    read: (connection, project) => clientFor(connection).projectLayout(project),
    write: async (connection, project, file, keepalive) => {
      try {
        await clientFor(connection).saveProjectLayout(project, file, keepalive);
      } catch (error) {
        const plain = error instanceof ApiError && error.status === 422 ? withoutSizes(file) : null;
        if (!plain) throw error;
        await clientFor(connection).saveProjectLayout(project, plain, keepalive);
      }
    },
  });
  /** Each project's first load since the page opened; an edit made before it is done waits for it. */
  const firstLoads = new Map<string, Promise<void>>();
  /** Projects whose file was read at least once, so what is shown came from it. */
  const everLoaded = new Set<string>();

  function setView(connection: string, project: string, view: ProjectEntry, edited: boolean) {
    set((s) => ({ projectViews: { ...s.projectViews, [connection]: { ...s.projectViews[connection], [project]: view } } }));
    if (edited) sync.edited(connection, project, toFile(view));
  }

  /** The view less tabs that can't show: a plugin's view it no longer has or that is off, a session that has gone. */
  function cleanView<V extends ProjectView>(view: V): V {
    const state = get();
    const on = new Set(state.plugins.filter((p) => p.state === "enabled").map((p) => p.id));
    const gone = (content: TabContent) =>
      isStaleTab(content) ||
      (state.pluginsLoaded && content.kind === "plugin" && !on.has(content.plugin)) ||
      (state.sessionsLoaded && content.kind === "session" && !state.sessions[content.sessionId]);
    const layout = tabs.closeWhere(view.layout, gone);
    return layout === view.layout ? view : { ...view, layout };
  }

  /** Sessions' tabs and the panels as this browser kept them before layout.json, for the default project's first file. */
  function legacyView(connection: string): ProjectView | null {
    const state = get();
    const selected = state.selected[connection];
    const layout = state.layouts[connection]?.sessions ?? (selected ? tabs.open(tabs.emptyLayout(), { kind: "session", sessionId: selected }, "left") : null);
    if (!layout) return null;
    const panels = savedPanels() ?? { leftOpen: true, rightOpen: true };
    return { ...panels, layout };
  }

  function dropLegacy(connection: string) {
    const { sessions: _moved, ...rest } = get().layouts[connection] ?? {};
    saveLayouts({ ...get().layouts, [connection]: rest });
  }

  /** Read a project's layout.json and show it, unless changed here meanwhile; it lands on that connection's project only. */
  function loadProject(connection: string, project: string): Promise<void> {
    const key = `${connection}\n${project}`;
    const existing = get().projectViews[connection]?.[project];
    if (!existing) setView(connection, project, { ...defaultView(), ready: false }, false);
    const run = (async () => {
      // Edits made while the file couldn't be read were made on the defaults, not on it: the file wins.
      if (!everLoaded.has(key)) sync.discard(connection, project);
      const result = await sync.load(connection, project);
      const now = get().projectViews[connection]?.[project] ?? { ...defaultView(), ready: false };
      if (result.kind === "failed") {
        // The service is away, or older and without the route: show what there is and save nothing.
        if (!now.ready) setView(connection, project, { ...((project === DEFAULT_PROJECT && legacyView(connection)) || now), ready: true }, false);
        return;
      }
      everLoaded.add(key);
      if (result.kind === "stale") {
        sync.allowWrites(connection, project);
        if (!now.ready) setView(connection, project, { ...now, ready: true }, false);
        return;
      }
      const parsed = fromFile(result.file);
      if (parsed === "newer") {
        sync.forbidWrites(connection, project);
        setView(connection, project, { ...defaultView(), ready: true }, false);
        return;
      }
      sync.allowWrites(connection, project);
      const legacy = !parsed && result.file === null && project === DEFAULT_PROJECT ? legacyView(connection) : null;
      const read = parsed ?? legacy ?? defaultView();
      const view = connection === get().activeId ? cleanView(read) : read;
      const save = !!legacy || view !== read;
      if (now.ready && !save && sameView(now, view)) return;
      setView(connection, project, { ...view, ready: true }, save);
      if (legacy) dropLegacy(connection);
    })();
    if (!existing) firstLoads.set(key, run);
    return run;
  }

  /** Change a project's view and save it. Before its file has first been read, the change waits for it. */
  function updateProject(connection: string, project: string, change: (view: ProjectView) => ProjectView, after?: (view: ProjectView) => void) {
    const entry = get().projectViews[connection]?.[project];
    if (!entry?.ready) {
      const first = firstLoads.get(`${connection}\n${project}`) ?? loadProject(connection, project);
      void first.then(() => updateProject(connection, project, change, after));
      return;
    }
    const next = change(entry);
    if (next === entry) return;
    setView(connection, project, { ...next, ready: true }, true);
    after?.(next);
  }

  /** Apply a change to every project view of the active connection that is showing its file's tabs. */
  function updateReadyViews(change: (layout: Layout) => Layout) {
    const connection = get().activeId;
    for (const [project, entry] of Object.entries(get().projectViews[connection] ?? {})) {
      if (entry.ready) updateProject(connection, project, (view) => {
        const layout = change(view.layout);
        return layout === view.layout ? view : { ...view, layout };
      });
    }
  }

  /** Read again the layouts on screen: Sessions' (always on the page) and the project feature's shown. */
  function loadVisible() {
    const state = get();
    void loadProject(state.activeId, DEFAULT_PROJECT);
    const shown = projectOf(state, currentFeature(state));
    if (shown && shown !== DEFAULT_PROJECT) void loadProject(state.activeId, shown);
  }

  function saveScopes(scopes: AppState["scopes"]) {
    set({ scopes });
    try {
      localStorage.setItem(SCOPES_KEY, JSON.stringify(scopes));
    } catch {
      // Only a convenience.
    }
  }

  /**
   * The feature a session's tab opens in. For another project's session in a project feature
   * showing some other project, that feature switches to the session's project first.
   */
  function featureForSession(project?: string | null): FeatureId | null {
    const feature = featureForProject(get(), project);
    const fixed = FIXED_PROJECT[feature];
    if (fixed && project && project !== fixed) {
      console.warn(`A session of ${project} isn't opened in ${feature}`);
      return null;
    }
    if (project && project !== DEFAULT_PROJECT && isProjectFeature(feature) && projectOf(get(), feature) !== project) {
      void get().setProject(feature, project);
    }
    return feature;
  }

  async function loadPlugins(mine: number) {
    let plugins: PluginInfo[];
    try {
      plugins = await activeClient(get()).plugins();
    } catch {
      // An older service has no plugins. Any other failure shows up elsewhere.
      plugins = [];
    }
    if (mine !== attempt) return;
    set({ plugins, pluginsLoaded: true });
    closePluginTabs(plugins);
  }

  /** A plugin that is off takes its tabs with it, and its feature starts afresh when it is back. */
  function closePluginTabs(plugins: PluginInfo[]) {
    const on = new Set(plugins.filter((p) => p.state === "enabled").map((p) => p.id));
    const state = get();
    const mine = state.layouts[state.activeId] ?? {};
    const off = (content: TabContent) => content.kind === "plugin" && !on.has(content.plugin);
    updateReadyViews((layout) => tabs.closeWhere(layout, off));
    const next: Partial<Record<FeatureId, Layout>> = {};
    let changed = false;
    for (const [feature, layout] of Object.entries(mine) as [FeatureId, Layout][]) {
      const plugin = pluginOf(feature);
      if (plugin && !on.has(plugin)) {
        changed = true;
        continue;
      }
      const closed = tabs.closeWhere(layout, off);
      if (closed !== layout) changed = true;
      next[feature] = closed;
    }
    if (changed) saveLayouts({ ...state.layouts, [state.activeId]: next });
  }

  function saveLayouts(layouts: Layouts, selected = get().selected) {
    set({ layouts, selected });
    try {
      // A project's tabs are in its layout.json; Sessions' old ones stay here until they have moved there.
      const split = (noProject: boolean) =>
        Object.fromEntries(
          Object.entries(layouts).map(([id, byFeature]) => [
            id,
            Object.fromEntries(Object.entries(byFeature ?? {}).filter(([f]) => noProject === (f !== "sessions" && isProjectFeature(f as FeatureId)))),
          ]),
        );
      localStorage.setItem(TABS_KEY, JSON.stringify(split(false)));
      localStorage.setItem(NO_PROJECT_KEY, JSON.stringify(split(true)));
      localStorage.setItem(SELECTED_KEY, JSON.stringify(selected));
    } catch {
      // Only a convenience.
    }
  }

  function showFeature(feature: FeatureId) {
    const state = get();
    if (state.features[state.activeId] === feature) return;
    const leaving = projectOf(state, currentFeature(state));
    const entering = projectOf(state, feature);
    if (leaving && leaving !== entering) void sync.flush(state.activeId, leaving);
    if (entering) void loadProject(state.activeId, entering);
    const features = { ...state.features, [state.activeId]: feature };
    set({ features });
    try {
      localStorage.setItem(FEATURE_KEY, JSON.stringify(features));
    } catch {
      // Only a convenience.
    }
  }

  // Session events wait here briefly and are applied together. The service sends one per
  // changed session on each metadata tick, and one store change each re-rendered the app as often.
  let pending: { mine: number; events: Event[] } | null = null;

  function queueEvent(mine: number, event: Event) {
    if (pending?.mine !== mine) pending = { mine, events: [] };
    pending.events.push(event);
    if (pending.events.length === 1) setTimeout(flushEvents, EVENT_BATCH_MS);
  }

  function flushEvents() {
    const batch = pending;
    pending = null;
    if (!batch || batch.mine !== attempt) return;
    const snapshot = batch.events.some((e) => e.type === "snapshot");
    set({ sessions: batch.events.reduce(applyEvent, get().sessions), ...(snapshot ? { sessionsLoaded: true } : {}) });
    if (snapshot) {
      const { sessions } = get();
      updateReadyViews((layout) => tabs.closeWhere(layout, (c) => c.kind === "session" && !sessions[c.sessionId]));
    }
    for (const event of batch.events) {
      if (event.type !== "session.removed") continue;
      updateReadyViews((layout) => tabs.closeSession(layout, event.id));
      const features = Object.keys(get().layouts[get().activeId] ?? {}) as FeatureId[];
      for (const feature of features) if (!projectOf(get(), feature)) updateLayout((layout) => tabs.closeSession(layout, event.id), feature);
    }
  }

  function closeLink() {
    attempt += 1;
    socket?.close();
    socket = null;
  }

  async function openEvents(mine: number, service: ServiceInfo, delay = RETRY_MS.first, socketFailures = 0) {
    const retry = (failures: number) => {
      if (mine !== attempt) return;
      set({ link: { state: "reconnecting", service, socketBlocked: failures >= BLOCKED_AFTER } });
      setTimeout(() => {
        if (mine === attempt) void openEvents(mine, service, Math.min(delay * 2, RETRY_MS.max), failures);
      }, delay);
    };

    let url: string;
    try {
      // The token as it is now: it may have been renewed since this connection was opened.
      url = await activeClient(get()).eventsUrl();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) void connect(activeConnection(get()));
      else retry(0); // the service itself is away, which says nothing about its socket
      return;
    }
    if (mine !== attempt) return;

    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      if (mine === attempt) {
        delay = RETRY_MS.first;
        socketFailures = 0;
        set({ link: { state: "connected", service } });
        // On every open, so a reconnect also catches changes made while away.
        void loadPlugins(mine);
        loadVisible();
        // A restart, as after an update, may bring another version and app.
        void activeClient(get())
          .service()
          .then((fresh) => {
            if (mine !== attempt || get().link.state !== "connected") return;
            service = fresh;
            set({ link: { state: "connected", service: fresh } });
          })
          .catch(() => undefined);
      }
    };
    ws.onmessage = (message) => {
      if (mine !== attempt) return;
      const event = JSON.parse(message.data) as Event;
      if (event.type === "plugins") void loadPlugins(mine);
      else if (event.type === "plugin") dispatchPluginEvent(event.id, event.data);
      else queueEvent(mine, event);
    };
    ws.onclose = () => retry(socketFailures + 1);
  }

  /**
   * Change one feature's tabs on the active connection, by default the one shown.
   * The feature's selected session follows its focused tab.
   */
  function updateLayout(change: (layout: Layout) => Layout, feature: FeatureId = currentFeature(get())) {
    updateLayoutIn(ownerOf(get(), feature), change);
  }

  /** Change the tabs of one view, whichever is shown now: what a delayed action uses, so it lands where it began. */
  function updateLayoutIn({ connection, feature, project }: ViewOwner, change: (layout: Layout) => Layout) {
    const state = get();
    const follow = (layout: Layout, selected: Record<string, string>) => {
      const key = selectionKey(connection, feature, project);
      const focused = tabs.focusedTab(layout);
      const session = focused && tabs.sessionOf(focused.content);
      if (session) selected[key] = session;
      else if (!focused) delete selected[key];
      return selected;
    };
    if (project) {
      updateProject(
        connection,
        project,
        (view) => {
          const before = withFeaturePinned(view.layout, feature);
          const layout = change(before);
          return layout === before ? view : { ...view, layout };
        },
        (view) => saveSelected(follow(view.layout, { ...get().selected })),
      );
      return;
    }
    const before = withFeaturePinned(state.layouts[connection]?.[feature] ?? EMPTY_LAYOUT, feature);
    const layout = change(before);
    if (layout === before) return;
    const layouts = { ...state.layouts, [connection]: { ...state.layouts[connection], [feature]: layout } };
    saveLayouts(layouts, follow(layout, { ...state.selected }));
  }

  function saveSelected(selected: Record<string, string>) {
    set({ selected });
    try {
      localStorage.setItem(SELECTED_KEY, JSON.stringify(selected));
    } catch {
      // Only a convenience.
    }
  }

  function togglePanel(side: "left" | "right") {
    const state = get();
    const project = projectOf(state, currentFeature(state));
    if (project && state.projectViews[state.activeId]?.[project]?.ready && !narrow()) {
      updateProject(state.activeId, project, (view) => (side === "left" ? { ...view, leftOpen: !view.leftOpen } : { ...view, rightOpen: !view.rightOpen }));
      return;
    }
    const panels = side === "left" ? { leftOpen: !state.leftOpen, rightOpen: state.rightOpen } : { leftOpen: state.leftOpen, rightOpen: !state.rightOpen };
    set(panels);
    saveLayout({ ...get(), ...panels });
  }

  function setPanelWidth(side: "left" | "right", px: number) {
    const state = get();
    const width = clampSide(px);
    const key = side === "left" ? "leftWidth" : "rightWidth";
    const project = projectOf(state, currentFeature(state));
    if (project && state.projectViews[state.activeId]?.[project]?.ready) {
      updateProject(state.activeId, project, (view) => (view[key] === width ? view : { ...view, [key]: width }));
      return;
    }
    if (state[key] === width) return;
    set({ [key]: width });
    saveLayout(get());
  }

  function setSplit(feature: FeatureId, value: number) {
    const state = get();
    const share = clampSplit(value);
    const connection = state.activeId;
    const project = projectOf(state, feature);
    if (project) {
      updateProject(connection, project, (view) => (view.split === share ? view : { ...view, split: share }));
      return;
    }
    if (state.splits[connection]?.[feature] === share) return;
    const splits = { ...state.splits, [connection]: { ...state.splits[connection], [feature]: share } };
    set({ splits });
    try {
      localStorage.setItem(SPLITS_KEY, JSON.stringify(splits));
    } catch {
      // Only a convenience.
    }
  }

  async function connect(target: Connection) {
    closeLink();
    const mine = attempt;
    set({ link: { state: "connecting" }, sessions: {}, sessionsLoaded: false, plugins: [], pluginsLoaded: false, history: NO_HISTORY });
    // Another tab may have renewed the token while this one was asleep: don't call it signed out.
    await syncCredentials();
    if (mine !== attempt) return;
    const connection = get().connections.find((c) => c.id === target.id) ?? target;
    const result = await checkConnection(connection);
    if (mine !== attempt) return;
    if (result.update) get().updateConnection(connection.id, result.update);
    if (!result.ok) {
      set({ link: { state: "failed", failure: result } });
      return;
    }
    const { name } = result.service;
    // The service's own name, shown unless the user gave one. A connection added signed out has only its host.
    if (connection.label !== name) get().updateConnection(connection.id, { label: name });
    void openEvents(mine, result.service);
    // Over HTTP, so the tabs come back even when the event socket is blocked.
    loadVisible();
    // For the limit on active sessions, even with the left panel shut.
    void get().loadHistory();
  }

  /** Take credentials another tab saved, such as a token it renewed. Changes nothing else. */
  async function syncCredentials() {
    const merged = mergeCredentials(get().connections, await platform.loadConnections());
    if (merged !== get().connections) set({ connections: merged });
  }

  async function renewOne(id: string) {
    const run = async () => {
      await syncCredentials(); // another tab may have just renewed it
      const connection = get().connections.find((c) => c.id === id);
      if (!connection || !needsRenewal(connection)) return;
      try {
        const body = await new ServiceClient(connection.url, connection.token).renew();
        get().updateConnection(id, credentialsFrom(body));
      } catch (error) {
        // Unreachable: try on the next check, the token is still good for hours.
        if (!(error instanceof ApiError && error.status === 401)) return;
        get().updateConnection(id, EXPIRED);
        if (id === get().activeId) void connect(activeConnection(get()));
      }
    };
    // One tab renews, and the others take the new token from storage.
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    if (locks) await locks.request(`agentop-renew:${id}`, run);
    else await run();
  }

  let renewing = false;
  /** Renew every saved service's token that is due, while the app is on screen. */
  async function renewDue() {
    if (renewing || document.visibilityState === "hidden") return;
    renewing = true;
    try {
      for (const connection of get().connections) {
        if (needsRenewal(connection)) await renewOne(connection.id);
      }
    } finally {
      renewing = false;
    }
  }

  function watchCredentials() {
    setInterval(() => void renewDue(), RENEW_CHECK_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void renewDue();
    });
    window.addEventListener("focus", () => void renewDue());
    // A layout edited in the last moment before the page goes is saved on the way out.
    window.addEventListener("pagehide", () => void sync.flushAll(undefined, true));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") void sync.flushAll(undefined, true);
    });
    window.addEventListener("storage", (event) => {
      if (event.key === null || event.key === "agentop.connections") void syncCredentials();
    });
  }

  return {
    connections: [],
    features: loadFeatures(),
    settings: loadSettings(),
    activeId: "",
    link: { state: "connecting" },
    sessions: {},
    plugins: [],
    pluginsLoaded: false,
    history: NO_HISTORY,
    selected: loadSelected(),
    layouts: loadLayouts(),
    scopes: loadScopes(),
    projectViews: {},
    sessionsLoaded: false,
    ...loadLayout(),
    splits: loadSplits(),
    folded: loadFolded(),
    switcherOpen: false,
    dialog: null,
    error: null,

    async init() {
      const saved = await platform.loadConnections();
      const home = await platform.defaultConnection();
      let connections = saved;
      if (home) {
        const known = saved.find((c) => c.id === home.id);
        // The page's #token=, or the desktop app's local service.json, is the service token.
        const merged = { ...home, ...known, url: home.url, serviceToken: home.serviceToken || undefined };
        connections = [merged, ...saved.filter((c) => c.id !== home.id)];
      }
      persist(connections);
      const wanted = platform.loadActiveId();
      const activeId = connections.find((c) => c.id === wanted)?.id ?? connections[0]?.id ?? "";
      set({ connections, activeId });
      watchCredentials();
      if (activeId) await connect(activeConnection(get()));
      void renewDue();
    },

    switchTo(id) {
      void sync.flushAll(get().activeId);
      platform.saveActiveId(id);
      set({ activeId: id, switcherOpen: false, error: null });
      void connect(activeConnection(get()));
    },

    retry() {
      void connect(activeConnection(get()));
    },

    setToken(token) {
      get().updateConnection(get().activeId, { ...SIGNED_OUT, serviceToken: token });
      void connect(activeConnection(get()));
    },

    signIn(token) {
      get().updateConnection(get().activeId, credentialsFrom(token));
      void connect(activeConnection(get()));
    },

    async signOut() {
      const connection = activeConnection(get());
      if (connection.clientId && connection.token) {
        await new ServiceClient(connection.url, connection.token).revokeClient(connection.clientId).catch(() => undefined);
      }
      get().updateConnection(connection.id, SIGNED_OUT);
      void connect(activeConnection(get()));
    },

    setFeature(feature) {
      showFeature(feature);
    },

    async setProject(feature, project) {
      if (!isProjectFeature(feature) || FIXED_PROJECT[feature]) return;
      const state = get();
      const connection = state.activeId;
      const leaving = projectOf(state, feature);
      if (leaving && leaving !== project) void sync.flush(connection, leaving);
      const { [feature]: _old, ...rest } = state.scopes[connection] ?? {};
      saveScopes({ ...state.scopes, [connection]: project ? { ...rest, [feature]: project } : rest });
      if (project) await loadProject(connection, project);
    },

    async setPluginEnabled(id, enabled) {
      const updated = await get().act((client) => client.setPluginEnabled(id, enabled));
      if (updated) set({ plugins: get().plugins.map((p) => (p.id === id ? updated : p)) });
    },

    updateSettings(changes) {
      const settings = { ...get().settings, ...changes };
      saveSettings(settings);
      applyTheme(settings.theme);
      set({ settings });
    },


    markStopped() {
      closeLink();
      set({ link: { state: "failed", failure: { kind: "stopped" } }, sessions: {}, sessionsLoaded: false, plugins: [], pluginsLoaded: false });
    },

    addConnection(connection) {
      const connections = [...get().connections, connection];
      persist(connections);
      set({ connections, dialog: null });
      get().switchTo(connection.id);
    },

    updateConnection(id, fields) {
      const connections = get().connections.map((c) => (c.id === id ? { ...c, ...fields } : c));
      persist(connections);
      set({ connections });
    },

    removeConnection(id) {
      const connections = get().connections.filter((c) => c.id !== id);
      persist(connections);
      const { [id]: _layouts, ...layouts } = get().layouts;
      const { [id]: _feature, ...features } = get().features;
      const { [id]: _views, ...projectViews } = get().projectViews;
      const { [id]: _scopes, ...scopes } = get().scopes;
      set({ connections, features, projectViews });
      saveScopes(scopes);
      saveLayouts(layouts);
      if (get().activeId === id && connections[0]) get().switchTo(connections[0].id);
    },

    select(sessionId, mode = "replace", area = "left") {
      const feature = featureForSession(get().sessions[sessionId]?.project);
      if (!feature) return;
      updateLayout((layout) => tabs.open(layout, { kind: "session", sessionId }, area, mode), feature);
      showFeature(feature);
    },

    openTab(content, area, mode = "replace") {
      const feature = content.kind === "session" ? featureForSession(get().sessions[content.sessionId]?.project) : currentFeature(get());
      if (!feature) return;
      updateLayout((layout) => tabs.open(layout, content, area, mode), feature);
      showFeature(feature);
    },
    openTabIn(owner, content, area, mode = "replace") {
      const project = content.kind === "session" ? get().sessions[content.sessionId]?.project : undefined;
      if (owner.project && project && project !== owner.project) return;
      updateLayoutIn(owner, (layout) => tabs.open(layout, content, area, mode));
    },
    focusAreaIn: (owner, area) => updateLayoutIn(owner, (layout) => tabs.focusArea(layout, area)),
    closeTabsIn: (owner, matches) => updateLayoutIn(owner, (layout) => tabs.closeWhere(layout, matches)),
    closeTabs: (feature, matches) => updateLayout((layout) => tabs.closeWhere(layout, matches), feature),
    activateTab: (feature, area, tabId) => updateLayout((layout) => tabs.activate(layout, area, tabId), feature),
    closeTab: (feature, area, tabId) => updateLayout((layout) => tabs.close(layout, area, tabId), feature),
    moveTab: (feature, area, tabId) => updateLayout((layout) => tabs.moveToOther(layout, area, tabId), feature),
    focusArea: (feature, area) => updateLayout((layout) => tabs.focusArea(layout, area), feature),
    activateTabAt: (index) => updateLayout((layout) => tabs.activateAt(layout, index)),
    closeActiveTab: () => updateLayout((layout) => tabs.closeActive(layout)),

    async loadHistory() {
      const listing = await get().act((client) => client.history());
      if (!listing) return;
      set({ history: { items: listing.items, windowSeconds: listing.window_seconds, maxActive: listing.max_active } });
    },

    async resumeHistory(item) {
      // The past session's tab is in the view shown now; the reply may come after another is.
      const owner = ownerOf(get());
      const created = await get().act((client) => client.resumeHistory(item.id));
      if (!created) return;
      const past = (content: TabContent) => content.kind === "past" && content.historyId === item.id;
      const session: TabContent = { kind: "session", sessionId: created.id };
      const into = owner.project && item.project && owner.project !== item.project ? null : owner;
      if (into) updateLayoutIn(into, (layout) => tabs.open(tabs.mapContent(layout, (c) => (past(c) ? session : c)), session, "left"));
      if (get().activeId === owner.connection) void get().loadHistory();
    },

    async removeHistory(item) {
      const done = await get().act((client) => client.deleteHistory(item.id));
      if (done === undefined) return;
      updateLayout((layout) => tabs.closeWhere(layout, (c) => c.kind === "past" && c.historyId === item.id), "sessions");
      await get().loadHistory();
    },

    setPanelWidth,
    setSplit,
    toggleLeft: () => togglePanel("left"),
    toggleRight: () => togglePanel("right"),
    toggleArea: (area) => updateLayout((layout) => tabs.toggleArea(layout, area)),

    setFolded(section, shut) {
      const folded = get().folded.filter((s) => s !== section).concat(shut ? [section] : []);
      set({ folded });
      try {
        localStorage.setItem(FOLDED_KEY, JSON.stringify(folded));
      } catch {
        // Only a convenience.
      }
    },

    setSwitcherOpen: (switcherOpen) => set({ switcherOpen }),
    setDialog: (dialog) => set({ dialog, switcherOpen: false }),
    openStatus(id) {
      set({ switcherOpen: false, dialog: null });
      if (id !== get().activeId) get().switchTo(id);
      updateLayout((layout) => tabs.open(layout, { kind: "status" }, "left"));
    },
    setError: (error) => set({ error }),

    async act(action) {
      try {
        const result = await action(activeClient(get()));
        set({ error: null });
        return result;
      } catch (error) {
        // Revoked or run out: the connection check says which, and offers to sign in again.
        if (error instanceof ApiError && error.status === 401) void connect(activeConnection(get()));
        else set({ error: error instanceof Error ? error.message : String(error) });
        return undefined;
      }
    },
  };
});
