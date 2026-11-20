import type { Connection } from "../connections";
import type { Platform } from "./index";

/**
 * The desktop app. It runs this computer's service and shows the page that service
 * serves, so this code comes from the service, not from the app. Connections stay in
 * the window's storage, except client tokens, which go to the OS keychain. Nothing else
 * in the UI knows which shell it runs in.
 */

/**
 * The revision of the app's native commands this UI needs (NATIVE_API in
 * desktop/src-tauri/src/main.rs). An app below it must be updated before it works fully.
 */
export const NATIVE_API = 2;

const SAVED_KEY = "agentop.connections";
const ACTIVE_KEY = "agentop.active";
const HOME_ID = "home";

interface ServiceInfo {
  url: string;
  token: string;
  name: string;
}

async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const tauri = await import("@tauri-apps/api/core");
  return tauri.invoke<T>(command, args);
}

const serviceInfo = () => invoke<ServiceInfo | null>("read_service_info");

interface DesktopInfo {
  version: string;
  native_api: number;
  releases_url: string;
}

const RELEASES_URL = "https://github.com/lulurun/agentop/releases";

/** An app from before desktop_info has none of the newer commands. */
async function desktopInfo(): Promise<DesktopInfo> {
  try {
    return await invoke<DesktopInfo>("desktop_info");
  } catch {
    return { version: "0.0.0", native_api: 0, releases_url: RELEASES_URL };
  }
}

function parseVersion(text: string): number[] {
  const parts = text.split(".").map((p) => parseInt(p, 10) || 0);
  return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0];
}

/** Negative, zero or positive, as a is older than, the same as, or newer than b. */
export function compareVersions(a: string, b: string): number {
  const [x, y] = [parseVersion(a), parseVersion(b)];
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
}

/** The address of the service that served this page, or, before it has, its configured one. */
function homeUrl(service: ServiceInfo | null): string {
  if (location.protocol === "http:" || location.protocol === "https:") return location.origin;
  return service?.url ?? "http://127.0.0.1:9775";
}

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
      else if (previous.some((p) => p.id === connection.id)) void invoke("token_delete", { id: connection.id }).catch(() => undefined);
    }
  },

  async defaultConnection() {
    const service = await localService();
    // Shown even with no service, so that the app has somewhere to explain that.
    if (!service) return { id: HOME_ID, label: "This computer", url: homeUrl(null), token: "", isHome: true };
    return {
      id: HOME_ID,
      label: service.name,
      url: homeUrl(service),
      token: "",
      // Read from this computer's service.json, so the desktop app can always sign in again.
      serviceToken: service.token,
      isHome: true,
    };
  },

  loadActiveId: () => localStorage.getItem(ACTIVE_KEY),
  saveActiveId: (id) => localStorage.setItem(ACTIVE_KEY, id),
  // Natively, because WebKit lets a page write the clipboard only during a click or
  // key press, and a copy made in tmux arrives later, over the terminal socket.
  async copyText(text) {
    const clipboard = await import("@tauri-apps/plugin-clipboard-manager");
    await clipboard.writeText(text);
  },

  /** A new major version is the app's own: a download. Any other release only updates this computer's agentop, in place. */
  async checkAppUpdate(release) {
    const info = await desktopInfo();
    if (info.native_api < NATIVE_API) return { kind: "app", version: release?.latest ?? "", required: true, releaseUrl: info.releases_url };
    if (!release) return null;
    if (parseVersion(release.latest)[0] > parseVersion(info.version)[0]) {
      return { kind: "app", version: release.latest, required: false, releaseUrl: `${info.releases_url}/latest` };
    }
    // The app's own version is out too, as the tag it was built from: a new app can find its agentop older.
    const target = compareVersions(info.version, release.latest) > 0 ? info.version : release.latest;
    if (compareVersions(target, release.current) <= 0) return null;
    return { kind: "service", version: target, required: false, releaseUrl: info.releases_url, install: () => invoke("update_open") };
  },

  /** Resolves once the service answers, or rejects with why it didn't. */
  async startService() {
    await invoke("service_start");
    for (;;) {
      const status = await invoke<{ state: string; message?: string }>("startup_status");
      if (status.state === "ready") return;
      if (status.state === "failed") throw new Error(status.message ?? "The service didn't start.");
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  },

  async openUrl(url) {
    const opener = await import("@tauri-apps/plugin-opener");
    await opener.openUrl(url);
  },
};
