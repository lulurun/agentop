import { addClient, ApiError, fetchHealth, NetworkError, ServiceClient, type ClientToken, type ServiceInfo } from "./api/client";

/**
 * The /api/v2 revision this app was built for. A service only ever adds to /api/v2, so the app
 * works with any revision; a higher one means this app is out of date. See docs/v2-design.md, section 4.5.
 */
export const API_VERSION = 1;

export function appIsOutdated(service: ServiceInfo): boolean {
  return service.api_version > API_VERSION;
}

export interface Connection {
  id: string;
  label: string;
  url: string;
  /** This app's own client token for the service, which expires unless renewed. Empty when signed out. */
  token: string;
  /** The service's ID for this app as a client. Kept after the token expires, to say so. */
  clientId?: string;
  /** When the token was issued and when it runs out, by this device's clock (ms). */
  issuedAt?: number;
  expiresAt?: number;
  /** The service's token lifetime, in seconds. */
  ttl?: number;
  /**
   * The service token, held only until it has signed this app in. Never saved:
   * persist() drops it. The desktop app reads it from this computer's service.json.
   */
  serviceToken?: string;
  /** Lists the service that served this page, which cannot be removed. */
  isHome?: boolean;
  /** A name the user gave it in this app. Without one, the service's own name shows. */
  alias?: string;
}

/** What the app calls a saved service: the user's name for it, or else the service's. */
export function connectionName(connection: Connection, serviceName?: string): string {
  return connection.alias?.trim() || serviceName || connection.label;
}

/** Renew once a token is a twelfth of its lifetime old, but no more often than every five minutes. */
export const RENEW_CHECK_MS = 5 * 60_000;

export function renewAgeMs(ttlSeconds: number): number {
  return Math.max((ttlSeconds * 1000) / 12, RENEW_CHECK_MS);
}

export function hasLiveToken(connection: Connection, now = Date.now()): boolean {
  return !!connection.token && !!connection.clientId && (connection.expiresAt ?? 0) > now;
}

export function needsRenewal(connection: Connection, now = Date.now()): boolean {
  if (!hasLiveToken(connection, now) || !connection.ttl) return false;
  return now - (connection.issuedAt ?? 0) >= renewAgeMs(connection.ttl);
}

export function credentialsFrom(body: ClientToken, now = Date.now()): Partial<Connection> {
  return {
    token: body.token,
    clientId: body.client_id,
    issuedAt: now,
    expiresAt: now + body.expires_in * 1000,
    ttl: body.ttl,
    serviceToken: undefined,
  };
}

/** The token ran out or was revoked. The client ID stays, so the app can say why it's signed out. */
export const EXPIRED: Partial<Connection> = { token: "", issuedAt: undefined, expiresAt: undefined, ttl: undefined };
export const SIGNED_OUT: Partial<Connection> = { ...EXPIRED, clientId: undefined, serviceToken: undefined };

/** What the service lists this app as. */
export function clientName(): string {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  const desktop = "__TAURI_INTERNALS__" in window;
  return desktop ? `agentop desktop${os ? ` on ${os}` : ""}` : `${browser}${os ? ` on ${os}` : ""}`;
}

export type ConnectFailure =
  | { kind: "unreachable" }
  | { kind: "stopped" }
  | { kind: "not_agentop" }
  | { kind: "no_token" }
  | { kind: "expired" }
  | { kind: "token_rejected" }
  | { kind: "rate_limited" };

/** `update` holds changes to the connection's credentials, which the caller saves. */
export type ConnectResult = ({ ok: true; service: ServiceInfo } | ({ ok: false } & ConnectFailure)) & {
  update?: Partial<Connection>;
};

/** Accepts a plain URL, or the `url#token=…` string from `agentop service connect-info`. */
export function parseConnectInfo(input: string): { url: string; token: string } | null {
  const text = input.trim();
  const [address, fragment = ""] = text.split("#", 2);
  let parsed: URL;
  try {
    // Without a scheme, loopback is plain HTTP and anything else HTTPS: the app refuses plain HTTP elsewhere.
    parsed = new URL(/^https?:\/\//i.test(address) ? address : `http://${address}`);
    if (!/^https?:\/\//i.test(address) && !isLoopback(parsed.href)) parsed = new URL(`https://${address}`);
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

/** Plain HTTP to another machine: the token and every terminal could be read on the network, so the app refuses it. */
export function isPlainRemote(url: string): boolean {
  return !isEncrypted(url) && !isLoopback(url);
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

/** Whatever answers at the address is the service: a host runs one, so there is no identity to check. */
export async function checkConnection(connection: Connection): Promise<ConnectResult> {
  try {
    const health = await fetchHealth(connection.url);
    if (health.status !== "ok") return { ok: false, kind: "not_agentop" };
  } catch (error) {
    if (error instanceof NetworkError) return { ok: false, kind: "unreachable" };
    return { ok: false, kind: "not_agentop" };
  }
  let update: Partial<Connection> | undefined;
  let token = connection.token;
  if (!hasLiveToken(connection)) {
    // A connection saved before client tokens holds the service token itself, which now only signs in.
    const serviceToken = connection.serviceToken || (!connection.clientId ? connection.token : "");
    if (!serviceToken) {
      const signedOut = connection.token ? EXPIRED : undefined;
      return { ok: false, kind: connection.clientId ? "expired" : "no_token", update: signedOut };
    }
    try {
      update = credentialsFrom(await addClient(connection.url, serviceToken, clientName()));
      token = update.token!;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return { ok: false, kind: "token_rejected", update: SIGNED_OUT };
      if (error instanceof ApiError && error.status === 429) return { ok: false, kind: "rate_limited", update: SIGNED_OUT };
      return { ok: false, kind: "unreachable" };
    }
  }
  try {
    const service = await new ServiceClient(connection.url, token).service();
    return { ok: true, service, update };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return { ok: false, kind: "expired", update: EXPIRED };
    return { ok: false, kind: "unreachable", update };
  }
}

export function failureMessage(failure: ConnectFailure, label: string): { title: string; detail: string } {
  switch (failure.kind) {
    case "unreachable":
      return {
        title: `Can't reach ${label}`,
        detail:
          "The service may be stopped, a tunnel may be down, or your browser may be blocking the connection. If it's stopped, start it on that machine with agentop serve, or with the system service that runs it.",
      };
    case "stopped":
      return {
        title: `${label} is stopped`,
        detail: "Its sessions are still running. Start it on that machine with agentop serve, or with the system service that runs it, then try again.",
      };
    case "not_agentop":
      return { title: "This is not an agentop service", detail: "Check the address." };
    case "no_token":
      return {
        title: `Enter the token for ${label}`,
        detail: "On that machine, run agentop service token.",
      };
    case "expired":
      return {
        title: `Signed out of ${label}`,
        detail: "This app's access ran out after a while unused, or was revoked. Enter the service token to sign in again: on that machine, run agentop service token.",
      };
    case "token_rejected":
      return {
        title: `${label} rejected the token`,
        detail: `Each service has its own token. Run agentop service token on ${label} itself, not on this computer.`,
      };
    case "rate_limited":
      return {
        title: `${label} refused for now`,
        detail: "Too many wrong tokens were tried. Wait a minute, then try again.",
      };
  }
}
