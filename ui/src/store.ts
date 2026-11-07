import { create } from "zustand";
import { ApiError, ServiceClient, type Session, type ServiceInfo } from "./api/client";
import { checkConnection, type ConnectFailure, type Connection } from "./connections";
import { platform } from "./platform";

export type LinkState =
  | { state: "connecting" }
  | { state: "connected"; service: ServiceInfo }
  | { state: "reconnecting"; service: ServiceInfo }
  | { state: "failed"; failure: ConnectFailure };

type Dialog = "new-session" | "add-service" | "manage-services" | null;

interface AppState {
  connections: Connection[];
  activeId: string;
  link: LinkState;
  sessions: Record<string, Session>;
  /** Selected session per connection. Session IDs are only unique within one service. */
  selected: Record<string, string>;
  leftOpen: boolean;
  rightOpen: boolean;
  switcherOpen: boolean;
  dialog: Dialog;
  error: string | null;

  init(): void;
  switchTo(id: string): void;
  retry(trustNewIdentity?: boolean): void;
  setToken(token: string): void;
  addConnection(connection: Connection): void;
  updateConnection(id: string, fields: Partial<Connection>): void;
  removeConnection(id: string): void;
  select(sessionId: string): void;
  toggleLeft(): void;
  toggleRight(): void;
  setSwitcherOpen(open: boolean): void;
  setDialog(dialog: Dialog): void;
  setError(error: string | null): void;
  /** Run an action against the service, showing any failure instead of throwing. */
  act<T>(action: (client: ServiceClient) => Promise<T>): Promise<T | undefined>;
}

const LAYOUT_KEY = "agentop.layout";
const SELECTED_KEY = "agentop.selected";

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

function persist(connections: Connection[]) {
  platform.saveConnections(connections.filter((c) => !c.isHome || c.token));
}

type Event =
  | { type: "snapshot"; sessions: Session[] }
  | { type: "session.upserted"; session: Session }
  | { type: "session.removed"; id: string };

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
  }
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

export function activeConnection(state: Pick<AppState, "connections" | "activeId">): Connection {
  return state.connections.find((c) => c.id === state.activeId)!;
}

export function activeClient(state: Pick<AppState, "connections" | "activeId">): ServiceClient {
  const connection = activeConnection(state);
  return new ServiceClient(connection.url, connection.token);
}

const RETRY_MS = { first: 500, max: 10_000 };

export const useApp = create<AppState>((set, get) => {
  // Each connect attempt gets a number, and anything from an older attempt is
  // ignored. Without this, a slow service could overwrite the one switched to.
  let attempt = 0;
  let socket: WebSocket | null = null;

  function closeLink() {
    attempt += 1;
    socket?.close();
    socket = null;
  }

  async function openEvents(mine: number, connection: Connection, service: ServiceInfo, delay = RETRY_MS.first) {
    const client = new ServiceClient(connection.url, connection.token);
    const retry = () => {
      if (mine !== attempt) return;
      set({ link: { state: "reconnecting", service } });
      setTimeout(() => {
        if (mine === attempt) void openEvents(mine, connection, service, Math.min(delay * 2, RETRY_MS.max));
      }, delay);
    };

    let url: string;
    try {
      url = await client.eventsUrl();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) void connect(connection);
      else retry();
      return;
    }
    if (mine !== attempt) return;

    const ws = new WebSocket(url);
    socket = ws;
    ws.onopen = () => {
      if (mine === attempt) {
        delay = RETRY_MS.first;
        set({ link: { state: "connected", service } });
      }
    };
    ws.onmessage = (message) => {
      if (mine === attempt) set({ sessions: applyEvent(get().sessions, JSON.parse(message.data)) });
    };
    ws.onclose = retry;
  }

  async function connect(connection: Connection, trustNewIdentity = false) {
    closeLink();
    const mine = attempt;
    set({ link: { state: "connecting" }, sessions: {} });
    const result = await checkConnection(connection, trustNewIdentity);
    if (mine !== attempt) return;
    if (!result.ok) {
      set({ link: { state: "failed", failure: result } });
      return;
    }
    const { instance_id: instanceId, name } = result.health;
    if (connection.instanceId !== instanceId || (connection.isHome && connection.label !== name)) {
      get().updateConnection(connection.id, { instanceId, ...(connection.isHome ? { label: name } : {}) });
    }
    void openEvents(mine, activeConnection(get()), result.service);
  }

  return {
    connections: [],
    activeId: "",
    link: { state: "connecting" },
    sessions: {},
    selected: loadSelected(),
    ...loadLayout(),
    switcherOpen: false,
    dialog: null,
    error: null,

    init() {
      const saved = platform.loadConnections();
      const home = platform.defaultConnection();
      let connections = saved;
      if (home) {
        const known = saved.find((c) => c.id === home.id);
        const merged = { ...home, ...known, url: home.url, token: home.token || known?.token || "" };
        connections = [merged, ...saved.filter((c) => c.id !== home.id)];
      }
      persist(connections);
      const wanted = platform.loadActiveId();
      const activeId = connections.find((c) => c.id === wanted)?.id ?? connections[0]?.id ?? "";
      set({ connections, activeId });
      if (activeId) void connect(activeConnection(get()));
    },

    switchTo(id) {
      platform.saveActiveId(id);
      set({ activeId: id, switcherOpen: false, error: null });
      void connect(activeConnection(get()));
    },

    retry(trustNewIdentity = false) {
      void connect(activeConnection(get()), trustNewIdentity);
    },

    setToken(token) {
      get().updateConnection(get().activeId, { token });
      void connect(activeConnection(get()));
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
      set({ connections });
      if (get().activeId === id && connections[0]) get().switchTo(connections[0].id);
    },

    select(sessionId) {
      const selected = { ...get().selected, [get().activeId]: sessionId };
      set({ selected });
      try {
        localStorage.setItem(SELECTED_KEY, JSON.stringify(selected));
      } catch {
        // Only a convenience.
      }
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

    setSwitcherOpen: (switcherOpen) => set({ switcherOpen }),
    setDialog: (dialog) => set({ dialog, switcherOpen: false }),
    setError: (error) => set({ error }),

    async act(action) {
      try {
        const result = await action(activeClient(get()));
        set({ error: null });
        return result;
      } catch (error) {
        set({ error: error instanceof Error ? error.message : String(error) });
        return undefined;
      }
    },
  };
});
