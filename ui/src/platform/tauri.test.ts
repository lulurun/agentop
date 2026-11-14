/// <reference types="vite/client" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "../connections";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const SERVICE = { url: "http://127.0.0.1:9775", token: "local", instance_id: "this-machine", name: "pc" };
const respond = (service: unknown) =>
  invoke.mockImplementation(async (command: string) => (command === "read_service_info" ? service : null));
const connection = (fields: Partial<Connection>): Connection => ({ id: "x", label: "x", url: "http://127.0.0.1:9775", token: "t", ...fields });

async function platform() {
  vi.resetModules();
  return (await import("./tauri")).tauriPlatform;
}

beforeEach(() => {
  invoke.mockReset();
  localStorage.clear();
});

describe("connections", () => {
  it("keeps tokens out of window storage", async () => {
    respond(SERVICE);
    (await platform()).saveConnections([connection({ id: "a", token: "secret" })]);
    expect(localStorage.getItem("agentop.connections")).not.toContain("secret");
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("token_set", { id: "a", token: "secret" }));
  });

  it("removes the token when a connection is removed", async () => {
    respond(SERVICE);
    const app = await platform();
    app.saveConnections([connection({ id: "a" }), connection({ id: "b" })]);
    app.saveConnections([connection({ id: "b" })]);
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("token_delete", { id: "a" }));
  });

  it("uses this computer's service token only to sign in", async () => {
    respond(SERVICE);
    const home = await (await platform()).defaultConnection();
    expect(home).toMatchObject({ token: "", serviceToken: "local" });
  });

  it("drops a token from the keychain once it is gone", async () => {
    respond(SERVICE);
    const app = await platform();
    app.saveConnections([connection({ id: "a" })]);
    app.saveConnections([connection({ id: "a", token: "" })]);
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith("token_delete", { id: "a" }));
  });

  it("still has a home connection with no service here, to explain that", async () => {
    respond(null);
    const home = await (await platform()).defaultConnection();
    expect(home?.isHome).toBe(true);
    expect(home?.instanceId).toBeUndefined();
  });
});

it("keeps Tauri out of everything but the platform layer", () => {
  const sources = import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true });
  const offenders = Object.entries(sources)
    .filter(([path, text]) => !path.startsWith("./") && !path.endsWith(".d.ts") && String(text).includes("@tauri-apps"))
    .map(([path]) => path);
  expect(Object.keys(sources).length).toBeGreaterThan(10);
  expect(offenders).toEqual([]);
});
