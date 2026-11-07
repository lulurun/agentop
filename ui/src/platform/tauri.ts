import type { Connection } from "../connections";
import type { LocalControl, Platform } from "./index";

/**
 * The desktop app. Connections stay in the window's storage, except tokens, which
 * go to the OS keychain. Nothing else in the UI knows which shell it runs in.
 */

const SAVED_KEY = "agentop.connections";
const ACTIVE_KEY = "agentop.active";
const HOME_ID = "home";

interface ServiceInfo {
  url: string;
  token: string;
  instance_id: string;
  name: string;
}

interface ControlResult {
  installed: boolean;
  active: boolean;
  error: string | null;
}

async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = await import("@tauri-apps/api/core");
  return tauri.invoke<T>(command, args);
}

const serviceInfo = () => invoke<ServiceInfo | null>("read_service_info");

/** Asked for once: this machine's service doesn't change while the app runs. */
let local: Promise<ServiceInfo | null> | null = null;
const localService = () => (local ??= serviceInfo().catch(() => null));

export const tauriPlatform: Platform = {
  kind: "desktop",

  async loadConnections() {
    let saved: Connection[] = [];
    try {
      saved = JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]");
    } catch {
      return [];
    }
    return Promise.all(
      saved.map(async (connection) => ({
        ...connection,
        token: (await invoke<string | null>("token_get", { id: connection.id }).catch(() => null)) ?? "",
      })),
    );
  },

  saveConnections(connections) {
    const previous: Connection[] = JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]");
    const kept = new Set(connections.map((connection) => connection.id));
    for (const { id } of previous) {
      if (!kept.has(id)) void invoke("token_delete", { id }).catch(() => undefined);
    }
    const withoutTokens = connections.map((connection) => ({ ...connection, token: "" }));
    localStorage.setItem(SAVED_KEY, JSON.stringify(withoutTokens));
    for (const connection of connections) {
      if (connection.token) void invoke("token_set", { id: connection.id, token: connection.token }).catch(() => undefined);
    }
  },

  async defaultConnection() {
    const service = await localService();
    // Shown even with no service, so that the app has somewhere to explain that.
    if (!service) return { id: HOME_ID, label: "This computer", url: "http://127.0.0.1:9775", token: "", isHome: true };
    return {
      id: HOME_ID,
      label: service.name,
      url: service.url,
      token: service.token,
      instanceId: service.instance_id,
      isHome: true,
    };
  },

  async localControl(connection): Promise<LocalControl | null> {
    // By instance ID, never the address: a tunnel has a loopback address too.
    const service = await localService();
    if (!service || connection.instanceId !== service.instance_id) return null;
    const status = await invoke<ControlResult>("service_control", { action: "status" }).catch(() => null);
    if (!status?.installed) return null;
    return {
      async start() {
        const result = await invoke<ControlResult>("service_control", { action: "start" });
        if (result.error) throw new Error(result.error);
      },
    };
  },

  loadActiveId: () => localStorage.getItem(ACTIVE_KEY),
  saveActiveId: (id) => localStorage.setItem(ACTIVE_KEY, id),
  copyText: (text) => navigator.clipboard.writeText(text),
};
