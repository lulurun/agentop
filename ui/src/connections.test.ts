import { afterEach, describe, expect, it, vi } from "vitest";
import { checkConnection, newConnectionId, parseConnectInfo, reachabilityNote, type Connection } from "./connections";
import { applyEvent, sortedSessions } from "./store";

const HEALTH = { status: "ok", version: "1", api_version: 1, instance_id: "abc", name: "box" };
const SERVICE = { ...HEALTH, commit: null };
const conn: Connection = { id: "1", label: "box", url: "http://box:9775", token: "t" };

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
    expect(parseConnectInfo("box:9775/app/")).toEqual({ url: "http://box:9775", token: "" });
  });
  it("keeps a proxy path", () => {
    expect(parseConnectInfo("https://x.example/agentop/")?.url).toBe("https://x.example/agentop");
  });
  it("rejects nonsense", () => {
    expect(parseConnectInfo("http://")).toBeNull();
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

describe("checkConnection", () => {
  it("succeeds through every step", async () => {
    respond({ "/health": () => json(HEALTH), "/service": () => json(SERVICE) });
    expect((await checkConnection(conn)).ok).toBe(true);
  });
  it("reports unreachable", async () => {
    respond({});
    expect(await checkConnection(conn)).toEqual({ ok: false, kind: "unreachable" });
  });
  it("spots something else at that address", async () => {
    respond({ "/health": () => json({ hello: "world" }) });
    expect(await checkConnection(conn)).toEqual({ ok: false, kind: "not_agentop" });
    respond({ "/health": () => new Response("nope", { status: 404 }) });
    expect(await checkConnection(conn)).toEqual({ ok: false, kind: "not_agentop" });
  });
  it("never sends the token to a different service", async () => {
    const calls = respond({ "/health": () => json(HEALTH), "/service": () => json(SERVICE) });
    const result = await checkConnection({ ...conn, instanceId: "other" });
    expect(result).toMatchObject({ ok: false, kind: "identity_changed" });
    expect(calls.every((c) => c.auth === null)).toBe(true);
    expect(calls).toHaveLength(1);
  });
  it("continues once the user trusts the new service", async () => {
    respond({ "/health": () => json(HEALTH), "/service": () => json(SERVICE) });
    expect((await checkConnection({ ...conn, instanceId: "other" }, true)).ok).toBe(true);
  });
  it("refuses an unsupported API version", async () => {
    respond({ "/health": () => json({ ...HEALTH, api_version: 99 }) });
    expect(await checkConnection(conn)).toMatchObject({ ok: false, kind: "incompatible" });
  });
  it("asks for a missing token without calling /service", async () => {
    const calls = respond({ "/health": () => json(HEALTH) });
    expect(await checkConnection({ ...conn, token: "" })).toMatchObject({ ok: false, kind: "no_token" });
    expect(calls).toHaveLength(1);
  });
  it("reports a rejected token", async () => {
    respond({ "/health": () => json(HEALTH), "/service": () => json({ detail: "no" }, 401) });
    expect(await checkConnection(conn)).toMatchObject({ ok: false, kind: "token_rejected" });
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
