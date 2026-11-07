import type { Connection } from "../connections";
import { parseConnectInfo } from "../connections";
import type { Platform } from "./index";

const SAVED_KEY = "agentop.connections";
const ACTIVE_KEY = "agentop.active";
const HOME_ID = "home";

function read<T>(storage: Storage, key: string): T | null {
  try {
    const value = storage.getItem(key);
    return value === null ? null : (JSON.parse(value) as T);
  } catch {
    return null;
  }
}

function write(storage: Storage, key: string, value: unknown) {
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    // Private windows can refuse storage. The app still works for this visit.
  }
}

/** The address of the service that served this page, at any mount point. */
function homeUrl(): string {
  const path = location.pathname.replace(/\/(app\/?|app\/index\.html|index\.html)?$/, "");
  return `${location.origin}${path}`;
}

/** Take a token from `#token=…`, and remove it from the address bar at once. */
function consumeFragmentToken(): string {
  const parsed = parseConnectInfo(`${location.href.split("#")[0]}${location.hash}`);
  if (parsed?.token) history.replaceState(null, "", location.pathname + location.search);
  return parsed?.token ?? "";
}

async function copyWithFallback(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // navigator.clipboard needs a secure context, which a plain HTTP address is not.
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

export const webPlatform: Platform = {
  loadConnections: () => read<Connection[]>(localStorage, SAVED_KEY) ?? [],
  saveConnections: (connections) => write(localStorage, SAVED_KEY, connections),
  defaultConnection() {
    return { id: HOME_ID, label: "This service", url: homeUrl(), token: consumeFragmentToken(), isHome: true };
  },
  // Per tab first, so two tabs can show two services. A new tab starts on the last used.
  loadActiveId: () => read<string>(sessionStorage, ACTIVE_KEY) ?? read<string>(localStorage, ACTIVE_KEY),
  saveActiveId(id) {
    write(sessionStorage, ACTIVE_KEY, id);
    write(localStorage, ACTIVE_KEY, id);
  },
  copyText: copyWithFallback,
};
