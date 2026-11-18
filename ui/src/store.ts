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
import type { Layout, OpenMode, TabContent } from "./tabs";
import { isKnownWorkspace, isStaleTab, pluginOf, workspacesFor, type WorkspaceId } from "./workspaces";

export type LinkState =
  | { state: "connecting" }
  | { state: "connected"; service: ServiceInfo }
  | { state: "reconnecting"; service: ServiceInfo; socketBlocked?: boolean }
  | { state: "failed"; failure: ConnectFailure };

type Dialog = "new-session" | "add-service" | "settings" | null;

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

/** The sessions that count towards the limit: the ones agentop started, not a plugin's own. */
export function activeCount(sessions: Record<string, Session>): number {
  return Object.values(sessions).filter((s) => s.managed && !s.owner).length;
}

interface AppState {
  connections: Connection[];
  activeId: string;
  /** The workspace shown, per connection. */
  workspaces: Record<string, WorkspaceId>;
  settings: Settings;
  link: LinkState;
  sessions: Record<string, Session>;
  /** The connected service's plugins. Plugins differ by service, so this is reset on every switch. */
  plugins: PluginInfo[];
  /** The plugin list has come in since connecting, so a missing plugin workspace is really missing. */
  pluginsLoaded: boolean;
  history: HistoryState;
  /** Selected session per connection: the focused tab's session. Session IDs are only unique within one service. */
  selected: Record<string, string>;
  /** The main area's tabs per connection, one layout per workspace. */
  layouts: Record<string, Partial<Record<WorkspaceId, Layout>>>;
  leftOpen: boolean;
  rightOpen: boolean;
  /** The left panel's sections folded shut, by ID: `active`, `elsewhere`, `history`. */
  folded: string[];
  switcherOpen: boolean;
  dialog: Dialog;
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
  setWorkspace(workspace: WorkspaceId): void;
  setPluginEnabled(id: string, enabled: boolean): Promise<void>;
  updateSettings(changes: Partial<Settings>): void;
  /** The service was stopped on purpose, so don't keep trying to reach it. */
  markStopped(): void;
  addConnection(connection: Connection): void;
  updateConnection(id: string, fields: Partial<Connection>): void;
  removeConnection(id: string): void;
  /** Show a session in the Sessions workspace. By default it takes the place of the session shown now. */
  select(sessionId: string, mode?: OpenMode): void;
  /** Open a tab in the workspace shown; a session's tab always opens in Sessions. */
  openTab(content: TabContent, mode?: OpenMode): void;
  /** Change a workspace's tabs with a function of its layout, such as a plugin showing two tabs side by side. */
  editLayout(workspace: WorkspaceId, change: (layout: Layout) => Layout): void;
  activateTab(workspace: WorkspaceId, group: number, tabId: string): void;
  closeTab(workspace: WorkspaceId, group: number, tabId: string): void;
  splitTab(workspace: WorkspaceId): void;
  moveTab(workspace: WorkspaceId, group: number, tabId: string): void;
  focusGroup(workspace: WorkspaceId, group: number): void;
  loadHistory(): Promise<void>;
  /** Resume a past session; its tab becomes the new session's. */
  resumeHistory(item: HistoryItem): Promise<void>;
  /** Delete a past session's transcript from its tool, and close its tab. */
  removeHistory(item: HistoryItem): Promise<void>;
  toggleLeft(): void;
  toggleRight(): void;
  setFolded(section: string, folded: boolean): void;
  setSwitcherOpen(open: boolean): void;
  setDialog(dialog: Dialog): void;
  /** Open a service's Status tab in the workspace shown, switching to that service first. */
  openStatus(id: string): void;
  setError(error: string | null): void;
  /** Run an action against the service, showing any failure instead of throwing. */
  act<T>(action: (client: ServiceClient) => Promise<T>): Promise<T | undefined>;
}

const LAYOUT_KEY = "agentop.layout";
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
const WORKSPACE_KEY = "agentop.workspace";

type Layouts = AppState["layouts"];

/**
 * Saved tabs, per connection and workspace. Tabs saved before workspaces were
 * one layout per connection: those become the Sessions layout.
 */
export function parseLayouts(saved: Record<string, unknown>): Layouts {
  const result: Layouts = {};
  for (const [id, value] of Object.entries(saved ?? {})) {
    if (!value || typeof value !== "object") continue;
    if (Array.isArray((value as Layout).groups)) {
      const layout = tabs.parseLayout(value);
      if (layout) result[id] = { sessions: layout };
      continue;
    }
    const byWorkspace: Partial<Record<WorkspaceId, Layout>> = {};
    for (const [workspace, layout] of Object.entries(value as Record<string, unknown>)) {
      if (!isKnownWorkspace(workspace)) continue;
      const parsed = tabs.parseLayout(layout);
      if (parsed) byWorkspace[workspace as WorkspaceId] = tabs.closeWhere(parsed, isStaleTab);
    }
    result[id] = byWorkspace;
  }
  return result;
}

function loadLayouts(): Layouts {
  try {
    return parseLayouts(JSON.parse(localStorage.getItem(TABS_KEY) ?? "{}"));
  } catch {
    return {};
  }
}

function loadWorkspaces(): Record<string, WorkspaceId> {
  try {
    const saved = JSON.parse(localStorage.getItem(WORKSPACE_KEY) ?? "{}");
    return saved && typeof saved === "object" ? saved : {};
  } catch {
    return {};
  }
}

/**
 * The workspace shown for the active connection. One whose plugin is off, or
 * isn't on this service, falls back to Sessions once the plugin list is in.
 */
export function currentWorkspace(state: Pick<AppState, "workspaces" | "activeId" | "plugins" | "pluginsLoaded">): WorkspaceId {
  const saved = state.workspaces[state.activeId];
  const wanted = saved && isKnownWorkspace(saved) ? saved : "sessions";
  if (!pluginOf(wanted) || !state.pluginsLoaded) return wanted;
  return workspacesFor(state.plugins).some((w) => w.id === wanted) ? wanted : "sessions";
}

const EMPTY_LAYOUT = tabs.emptyLayout();
// A derived layout must be the same object each time, or every render would see a change.
const derived = new Map<string, Layout>();

function derive(key: string, make: () => Layout): Layout {
  if (!derived.has(key)) derived.set(key, make());
  return derived.get(key)!;
}

type LayoutState = Pick<AppState, "layouts" | "selected" | "activeId" | "workspaces" | "plugins" | "pluginsLoaded">;

/**
 * A workspace's tabs on the active connection, by default the one shown.
 * Before it has any, Sessions gets the session selected last time.
 */
export function layoutOf(state: LayoutState, workspace: WorkspaceId = currentWorkspace(state)): Layout {
  const saved = state.layouts[state.activeId]?.[workspace];
  if (saved) return saved;
  if (workspace !== "sessions") return EMPTY_LAYOUT;
  const selected = state.selected[state.activeId];
  if (!selected) return EMPTY_LAYOUT;
  return derive(`${state.activeId}\n${selected}`, () => tabs.open(tabs.emptyLayout(), { kind: "session", sessionId: selected }, "replace", "t0"));
}

function loadSelected(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(SELECTED_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function loadLayout(): { leftOpen: boolean; rightOpen: boolean } {
  const narrow = typeof window !== "undefined" && window.innerWidth < 900;
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null");
    if (saved && !narrow) return saved;
  } catch {
    // Fall through to the defaults.
  }
  return { leftOpen: !narrow, rightOpen: !narrow };
}

function saveLayout(layout: { leftOpen: boolean; rightOpen: boolean }) {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    // Only a convenience.
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

  /** A plugin that is off takes its tabs with it, and its workspace starts afresh when it is back. */
  function closePluginTabs(plugins: PluginInfo[]) {
    const on = new Set(plugins.filter((p) => p.state === "enabled").map((p) => p.id));
    const state = get();
    const mine = state.layouts[state.activeId];
    if (!mine) return;
    const off = (content: TabContent) => content.kind === "plugin" && !on.has(content.plugin);
    const next: Partial<Record<WorkspaceId, Layout>> = {};
    let changed = false;
    for (const [workspace, layout] of Object.entries(mine) as [WorkspaceId, Layout][]) {
      const plugin = pluginOf(workspace);
      if (plugin && !on.has(plugin)) {
        changed = true;
        continue;
      }
      const closed = tabs.closeWhere(layout, off);
      if (closed !== layout) changed = true;
      next[workspace] = closed;
    }
    if (changed) saveLayouts({ ...state.layouts, [state.activeId]: next });
  }

  function saveLayouts(layouts: Layouts, selected = get().selected) {
    set({ layouts, selected });
    try {
      localStorage.setItem(TABS_KEY, JSON.stringify(layouts));
      localStorage.setItem(SELECTED_KEY, JSON.stringify(selected));
    } catch {
      // Only a convenience.
    }
  }

  function showWorkspace(workspace: WorkspaceId) {
    const state = get();
    if (state.workspaces[state.activeId] === workspace) return;
    const workspaces = { ...state.workspaces, [state.activeId]: workspace };
    set({ workspaces });
    try {
      localStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspaces));
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
    set({ sessions: batch.events.reduce(applyEvent, get().sessions) });
    for (const event of batch.events) {
      if (event.type !== "session.removed") continue;
      const workspaces = new Set<WorkspaceId>(["sessions", ...(Object.keys(get().layouts[get().activeId] ?? {}) as WorkspaceId[])]);
      for (const workspace of workspaces) updateLayout((layout) => tabs.closeSession(layout, event.id), workspace);
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
   * Change one workspace's tabs on the active connection, by default the one shown.
   * In Sessions, the selected session follows the focused tab.
   */
  function updateLayout(change: (layout: Layout) => Layout, workspace: WorkspaceId = currentWorkspace(get())) {
    const state = get();
    const before = layoutOf(state, workspace);
    const layout = change(before);
    if (layout === before) return;
    const layouts = { ...state.layouts, [state.activeId]: { ...state.layouts[state.activeId], [workspace]: layout } };
    const selected = { ...state.selected };
    if (workspace === "sessions") {
      const focused = tabs.focusedTab(layout);
      const session = focused && tabs.sessionOf(focused.content);
      if (session) selected[state.activeId] = session;
      else if (!focused) delete selected[state.activeId];
    }
    saveLayouts(layouts, selected);
  }

  async function connect(target: Connection) {
    closeLink();
    const mine = attempt;
    set({ link: { state: "connecting" }, sessions: {}, plugins: [], pluginsLoaded: false, history: NO_HISTORY });
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
    window.addEventListener("storage", (event) => {
      if (event.key === null || event.key === "agentop.connections") void syncCredentials();
    });
  }

  return {
    connections: [],
    workspaces: loadWorkspaces(),
    settings: loadSettings(),
    activeId: "",
    link: { state: "connecting" },
    sessions: {},
    plugins: [],
    pluginsLoaded: false,
    history: NO_HISTORY,
    selected: loadSelected(),
    layouts: loadLayouts(),
    ...loadLayout(),
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

    setWorkspace(workspace) {
      showWorkspace(workspace);
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
      set({ link: { state: "failed", failure: { kind: "stopped" } }, sessions: {}, plugins: [], pluginsLoaded: false });
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
      const { [id]: _workspace, ...workspaces } = get().workspaces;
      set({ connections, workspaces });
      saveLayouts(layouts);
      if (get().activeId === id && connections[0]) get().switchTo(connections[0].id);
    },

    select(sessionId, mode = "replace") {
      updateLayout((layout) => tabs.open(layout, { kind: "session", sessionId }, mode), "sessions");
      showWorkspace("sessions");
    },

    openTab(content, mode = "tab") {
      const workspace = content.kind === "session" ? "sessions" : currentWorkspace(get());
      updateLayout((layout) => tabs.open(layout, content, mode), workspace);
      showWorkspace(workspace);
    },
    editLayout: (workspace, change) => updateLayout(change, workspace),
    activateTab: (workspace, group, tabId) => updateLayout((layout) => tabs.activate(layout, group, tabId), workspace),
    closeTab: (workspace, group, tabId) => updateLayout((layout) => tabs.close(layout, group, tabId), workspace),
    splitTab: (workspace) => updateLayout((layout) => tabs.split(layout), workspace),
    moveTab: (workspace, group, tabId) => updateLayout((layout) => tabs.moveToOther(layout, group, tabId), workspace),
    focusGroup: (workspace, group) => updateLayout((layout) => tabs.focusGroup(layout, group), workspace),

    async loadHistory() {
      const listing = await get().act((client) => client.history());
      if (!listing) return;
      set({ history: { items: listing.items, windowSeconds: listing.window_seconds, maxActive: listing.max_active } });
    },

    async resumeHistory(item) {
      const created = await get().act((client) => client.resumeHistory(item.id));
      if (!created) return;
      const past = (content: TabContent) => content.kind === "past" && content.historyId === item.id;
      const session: TabContent = { kind: "session", sessionId: created.id };
      updateLayout((layout) => {
        const swapped = {
          ...layout,
          groups: layout.groups.map((g) => ({ ...g, tabs: g.tabs.map((t) => (past(t.content) ? { ...t, content: session } : t)) })),
        };
        return tabs.open(swapped, session, "replace");
      }, "sessions");
      showWorkspace("sessions");
      void get().loadHistory();
    },

    async removeHistory(item) {
      const done = await get().act((client) => client.deleteHistory(item.id));
      if (done === undefined) return;
      updateLayout((layout) => tabs.closeWhere(layout, (c) => c.kind === "past" && c.historyId === item.id), "sessions");
      await get().loadHistory();
    },

    toggleLeft() {
      const leftOpen = !get().leftOpen;
      set({ leftOpen });
      saveLayout({ leftOpen, rightOpen: get().rightOpen });
    },

    toggleRight() {
      const rightOpen = !get().rightOpen;
      set({ rightOpen });
      saveLayout({ leftOpen: get().leftOpen, rightOpen });
    },

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
