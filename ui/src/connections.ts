import { ApiError, fetchHealth, NetworkError, ServiceClient, type Health, type ServiceInfo } from "./api/client";

export const SUPPORTED_API_VERSIONS = { min: 1, max: 1 };

export interface Connection {
  id: string;
  label: string;
  url: string;
  token: string;
  /** Pinned on first success, to notice a different service at the same address. */
  instanceId?: string;
  /** Lists the service that served this page, which cannot be removed. */
  isHome?: boolean;
}

export type ConnectFailure =
  | { kind: "unreachable" }
  | { kind: "not_agentop" }
  | { kind: "identity_changed"; health: Health }
  | { kind: "incompatible"; health: Health }
  | { kind: "no_token"; health: Health }
  | { kind: "token_rejected"; health: Health };

export type ConnectResult = { ok: true; health: Health; service: ServiceInfo } | ({ ok: false } & ConnectFailure);

/** Accepts a plain URL, or the `url#token=…` string from `agentop service connect-info`. */
export function parseConnectInfo(input: string): { url: string; token: string } | null {
  const text = input.trim();
  const [address, fragment = ""] = text.split("#", 2);
  const withScheme = /^https?:\/\//i.test(address) ? address : `http://${address}`;
  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return null;
  }
  if (!parsed.hostname) return null;
  const path = parsed.pathname.replace(/\/+$/, "").replace(/\/app$/, "");
  const token = new URLSearchParams(fragment).get("token") ?? "";
  return { url: `${parsed.protocol}//${parsed.host}${path}`, token };
}

/** What to use from the token box, which also accepts the whole connect-info string. */
export function tokenFromInput(input: string): string {
  const text = input.trim();
  return text.includes("#") ? (parseConnectInfo(text)?.token ?? "") : text;
}

/** crypto.randomUUID only exists in a secure context, which a plain HTTP address is not. */
export function newConnectionId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function isLoopback(url: string): boolean {
  const host = new URL(url).hostname.replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || host.startsWith("127.");
}

export function isEncrypted(url: string): boolean {
  return new URL(url).protocol === "https:";
}

export interface ReachabilityNote {
  blocked: boolean;
  message: string;
}

/** What browser rules will do to a connection from this page. See docs/v2-design.md, section 4.5. */
export function reachabilityNote(pageUrl: string, target: string): ReachabilityNote | null {
  const page = new URL(pageUrl);
  if (!/^https?:$/.test(page.protocol)) return null; // the desktop app has no such rules
  if (page.protocol === "https:" && !isEncrypted(target) && !isLoopback(target)) {
    return {
      blocked: true,
      message: "Browsers block plain HTTP from a secure page. Use an HTTPS address, or open this app from a local service.",
    };
  }
  if (isLoopback(target) && !isLoopback(pageUrl)) {
    return {
      blocked: page.protocol === "http:",
      message:
        page.protocol === "http:"
          ? "Browsers block this page from reaching this computer's own services. Open the app from a local service."
          : "Your browser may ask for permission to reach local services.",
    };
  }
  return null;
}

export async function checkConnection(connection: Connection, trustNewIdentity = false): Promise<ConnectResult> {
  let health: Health;
  try {
    health = await fetchHealth(connection.url);
  } catch (error) {
    if (error instanceof NetworkError) return { ok: false, kind: "unreachable" };
    return { ok: false, kind: "not_agentop" };
  }
  if (health.status !== "ok" || typeof health.api_version !== "number" || !health.instance_id) {
    return { ok: false, kind: "not_agentop" };
  }
  // Before the token is sent: this is what keeps it from going to the wrong service.
  if (connection.instanceId && connection.instanceId !== health.instance_id && !trustNewIdentity) {
    return { ok: false, kind: "identity_changed", health };
  }
  if (health.api_version < SUPPORTED_API_VERSIONS.min || health.api_version > SUPPORTED_API_VERSIONS.max) {
    return { ok: false, kind: "incompatible", health };
  }
  if (!connection.token) return { ok: false, kind: "no_token", health };
  try {
    const service = await new ServiceClient(connection.url, connection.token).service();
    return { ok: true, health, service };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { ok: false, kind: "token_rejected", health };
    return { ok: false, kind: "unreachable" };
  }
}

export function failureMessage(failure: ConnectFailure, label: string): { title: string; detail: string } {
  switch (failure.kind) {
    case "unreachable":
      return {
        title: `Can't reach ${label}`,
        detail: "The service may be stopped, a tunnel may be down, or your browser may be blocking the connection.",
      };
    case "not_agentop":
      return { title: "This is not an agentop service", detail: "Check the address." };
    case "identity_changed":
      return {
        title: "A different service is now at this address",
        detail: `It calls itself ${failure.health.name}. A reused tunnel port can cause this. Your token has not been sent to it.`,
      };
    case "incompatible":
      return {
        title: `${label} runs an incompatible version`,
        detail: `It runs ${failure.health.version}. Update the service or this app.`,
      };
    case "no_token":
      return {
        title: `Enter the token for ${failure.health.name}`,
        detail: "On that machine, run agentop service token.",
      };
    case "token_rejected":
      return {
        title: `${failure.health.name} rejected the token`,
        detail: `Each service has its own token. Run agentop service token on ${failure.health.name} itself, not on this computer.`,
      };
  }
}
