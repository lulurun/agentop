import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API_VERSION,
  appIsOutdated,
  checkConnection,
  isPlainRemote,
  needsRenewal,
  newConnectionId,
  parseConnectInfo,
  reachabilityNote,
  renewAgeMs,
  tokenFromInput,
  type Connection,
} from "./connections";
import { applyEvent, sortedSessions } from "./store";

const HEALTH = { status: "ok" };
const SERVICE = { version: "1", api_version: 1, name: "box", commit: null };
const HOUR = 3_600_000;
const conn: Connection = {
  id: "1",
  label: "box",
  url: "http://box:9775",
  token: "t",
  clientId: "c1",
  issuedAt: Date.now(),
  expiresAt: Date.now() + 12 * HOUR,
  ttl: 12 * 3600,
};
const ADDED = { client_id: "c2", token: "fresh", expires_in: 43200, ttl: 43200 };

function respond(routes: Record<string, () => Response | Promise<Response>>) {
  const calls: { url: string; auth: string | null }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const auth = new Headers(init?.headers).get("Authorization");
    calls.push({ url, auth });
    const key = Object.keys(routes).find((k) => url.endsWith(k));
    if (!key) throw new TypeError("network");
    return routes[key]();
  });
  return calls;
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.unstubAllGlobals());

describe("parseConnectInfo", () => {
  it("reads a connect-info string", () => {
    expect(parseConnectInfo(" http://box:9775#token=abc ")).toEqual({ url: "http://box:9775", token: "abc" });
  });
  it("defaults the scheme and drops /app", () => {
    expect(parseConnectInfo("box:9775/app/")).toEqual({ url: "https://box:9775", token: "" });
    expect(parseConnectInfo("127.0.0.1:9775")?.url).toBe("http://127.0.0.1:9775");
    expect(parseConnectInfo("localhost:9775")?.url).toBe("http://localhost:9775");
  });
  it("keeps a proxy path", () => {
    expect(parseConnectInfo("https://x.example/agentop/")?.url).toBe("https://x.example/agentop");
  });
  it("rejects nonsense", () => {
    expect(parseConnectInfo("http://")).toBeNull();
  });
});

describe("isPlainRemote", () => {
  it("is plain HTTP to anything but loopback", () => {
    expect(isPlainRemote("http://box:9775")).toBe(true);
    expect(isPlainRemote("https://box:9775")).toBe(false);
    expect(isPlainRemote("http://127.0.0.1:9775")).toBe(false);
    expect(isPlainRemote("http://[::1]:9775")).toBe(false);
  });
});

describe("tokenFromInput", () => {
  it("takes a bare token or the whole connect-info string", () => {
    expect(tokenFromInput("  abc123 ")).toBe("abc123");
    expect(tokenFromInput("http://203.0.113.9:7700/agentop#token=abc123")).toBe("abc123");
  });

  it("has no token to use from an address without one", () => {
    expect(tokenFromInput("http://203.0.113.9:7700/agentop#")).toBe("");
  });
});

describe("newConnectionId", () => {
  it("does not need a secure context", () => {
    vi.stubGlobal("crypto", { getRandomValues: (a: Uint8Array) => a.fill(255) });
    expect(newConnectionId()).toBe("ff".repeat(16));
  });
});

describe("reachabilityNote", () => {
  it("allows everything from a loopback page", () => {
    expect(reachabilityNote("http://127.0.0.1:9775/app/", "http://box:9775")).toBeNull();
    expect(reachabilityNote("http://127.0.0.1:9775/app/", "https://box")).toBeNull();
  });
  it("blocks plain HTTP from a secure page", () => {
    expect(reachabilityNote("https://x.example/app/", "http://box:9775")?.blocked).toBe(true);
  });
  it("warns about loopback from a secure page without blocking", () => {
    expect(reachabilityNote("https://x.example/app/", "http://127.0.0.1:9775")?.blocked).toBe(false);
  });
  it("blocks loopback from a plain HTTP LAN page", () => {
    expect(reachabilityNote("http://192.168.1.5:9775/app/", "http://127.0.0.1:9775")?.blocked).toBe(true);
  });
  it("has no opinion in the desktop app", () => {
    expect(reachabilityNote("tauri://localhost/", "http://box:9775")).toBeNull();
  });
});

describe("appIsOutdated", () => {
  it("says so only for a newer revision of /api/v2", () => {
    const at = (api_version: number) => ({ ...SERVICE, api_version }) as Parameters<typeof appIsOutdated>[0];
    expect(appIsOutdated(at(API_VERSION + 1))).toBe(true);
    expect(appIsOutdated(at(API_VERSION))).toBe(false);
    expect(appIsOutdated(at(API_VERSION - 1))).toBe(false);
  });
});

describe("checkConnection", () => {
  it("succeeds through every step", async () => {
    respond({ "/health": () => json(HEALTH), "/service": () => json(SERVICE) });
    expect((await checkConnection(conn)).ok).toBe(true);
  });
  it("reports unreachable", async () => {
    respond({});
    expect(await checkConnection(conn)).toMatchObject({ ok: false, kind: "unreachable" });
  });
  it("spots something else at that address", async () => {
    respond({ "/health": () => json({ hello: "world" }) });
    expect(await checkConnection(conn)).toEqual({ ok: false, kind: "not_agentop" });
    respond({ "/health": () => new Response("nope", { status: 404 }) });
    expect(await checkConnection(conn)).toEqual({ ok: false, kind: "not_agentop" });
  });
  it("asks /health without the token", async () => {
    const calls = respond({ "/health": () => json(HEALTH), "/service": () => json(SERVICE) });
    await checkConnection(conn);
    expect(calls[0].auth).toBeNull();
  });
  it("connects to a service of any revision of /api/v2", async () => {
    for (const api_version of [API_VERSION - 1, API_VERSION + 4]) {
      respond({ "/health": () => json(HEALTH), "/service": () => json({ ...SERVICE, api_version }) });
      expect((await checkConnection(conn)).ok).toBe(true);
    }
  });
  it("asks for a missing token without calling /service", async () => {
    const calls = respond({ "/health": () => json(HEALTH) });
    expect(await checkConnection({ ...conn, token: "", clientId: undefined })).toMatchObject({ ok: false, kind: "no_token" });
    expect(calls).toHaveLength(1);
  });
  it("signs in with the service token, and forgets it", async () => {
    const calls = respond({ "/health": () => json(HEALTH), "/auth/clients": () => json(ADDED, 201), "/service": () => json(SERVICE) });
    const result = await checkConnection({ id: "1", label: "box", url: "http://box:9775", token: "", serviceToken: "svc" });
    expect(result.ok).toBe(true);
    expect(result.update).toMatchObject({ token: "fresh", clientId: "c2", ttl: 43200, serviceToken: undefined });
    expect(calls.map((c) => c.auth)).toEqual([null, "Bearer svc", "Bearer fresh"]);
  });
  it("treats a token saved before client tokens as the service token", async () => {
    const calls = respond({ "/health": () => json(HEALTH), "/auth/clients": () => json(ADDED, 201), "/service": () => json(SERVICE) });
    const result = await checkConnection({ id: "1", label: "box", url: "http://box:9775", token: "old-service-token" });
    expect(result.update?.token).toBe("fresh");
    expect(calls[1].auth).toBe("Bearer old-service-token");
  });
  it("says so when its token ran out, without sending it", async () => {
    const calls = respond({ "/health": () => json(HEALTH) });
    const result = await checkConnection({ ...conn, expiresAt: Date.now() - 1 });
    expect(result).toMatchObject({ ok: false, kind: "expired", update: { token: "" } });
    expect(calls).toHaveLength(1);
  });
  it("says so when its token was revoked", async () => {
    respond({ "/health": () => json(HEALTH), "/service": () => json({ detail: "no" }, 401) });
    expect(await checkConnection(conn)).toMatchObject({ ok: false, kind: "expired", update: { token: "" } });
  });
  it("reports a rejected service token, and forgets it", async () => {
    respond({ "/health": () => json(HEALTH), "/auth/clients": () => json({ detail: "no" }, 401) });
    const result = await checkConnection({ ...conn, token: "", clientId: undefined, serviceToken: "wrong" });
    expect(result).toMatchObject({ ok: false, kind: "token_rejected", update: { serviceToken: undefined } });
  });
});

describe("renewal", () => {
  it("renews once a token is a twelfth of its lifetime old", () => {
    expect(renewAgeMs(12 * 3600)).toBe(HOUR);
    const now = Date.now();
    expect(needsRenewal({ ...conn, issuedAt: now - 59 * 60_000 }, now)).toBe(false);
    expect(needsRenewal({ ...conn, issuedAt: now - HOUR }, now)).toBe(true);
  });
  it("never more often than every five minutes", () => {
    expect(renewAgeMs(3600)).toBe(5 * 60_000);
    expect(renewAgeMs(60)).toBe(5 * 60_000);
  });
  it("not once it has run out: that needs the service token", () => {
    const now = Date.now();
    expect(needsRenewal({ ...conn, issuedAt: now - 13 * HOUR, expiresAt: now - HOUR }, now)).toBe(false);
  });
});

describe("session events", () => {
  const session = (id: string, created_at: number) => ({ id, created_at }) as never;
  it("replaces everything on a snapshot, and tracks changes after", () => {
    let sessions = applyEvent({ old: session("old", 1) }, { type: "snapshot", sessions: [session("a", 1)] });
    expect(Object.keys(sessions)).toEqual(["a"]);
    sessions = applyEvent(sessions, { type: "session.upserted", session: session("b", 2) });
    sessions = applyEvent(sessions, { type: "session.removed", id: "a" });
    expect(Object.keys(sessions)).toEqual(["b"]);
  });
  it("sorts newest first", () => {
    expect(sortedSessions({ a: session("a", 1), b: session("b", 2) }).map((s) => s.id)).toEqual(["b", "a"]);
  });
});
