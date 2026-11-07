/// <reference types="vite/client" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "../connections";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const SERVICE = { url: "http://127.0.0.1:9775", token: "local", instance_id: "this-machine", name: "pc" };
const respond = (service: unknown, status: { installed: boolean; active: boolean; error: string | null } = { installed: true, active: false, error: null }) =>
  invoke.mockImplementation(async (command: string) => {
    if (command === "read_service_info") return service;
    if (command === "service_control") return status;
    return null;
  });
const connection = (fields: Partial<Connection>): Connection => ({ id: "x", label: "x", url: "http://127.0.0.1:9775", token: "t", ...fields });

async function platform() {
  vi.resetModules();
  return (await import("./tauri")).tauriPlatform;
}

beforeEach(() => {
  invoke.mockReset();
  localStorage.clear();
});

describe("localControl", () => {
  it("is offered for this machine's service", async () => {
    respond(SERVICE);
    expect(await (await platform()).localControl(connection({ instanceId: "this-machine" }))).not.toBeNull();
  });

  it("is refused for a tunnel, even though its address is loopback", async () => {
    respond(SERVICE);
    const tunnel = connection({ url: "http://127.0.0.1:19775", instanceId: "another-machine" });
    expect(await (await platform()).localControl(tunnel)).toBeNull();
    expect(invoke).not.toHaveBeenCalledWith("service_control", expect.anything());
  });

  it("is refused for a connection that has never proved who it is", async () => {
    respond(SERVICE);
    expect(await (await platform()).localControl(connection({ instanceId: undefined }))).toBeNull();
  });

  it("is refused when this machine has no unit to start", async () => {
    respond(SERVICE, { installed: false, active: false, error: null });
    expect(await (await platform()).localControl(connection({ instanceId: "this-machine" }))).toBeNull();
  });

  it("reports a failed start", async () => {
    respond(SERVICE, { installed: true, active: false, error: "Unit failed" });
    const control = await (await platform()).localControl(connection({ instanceId: "this-machine" }));
    await expect(control!.start()).rejects.toThrow("Unit failed");
  });
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
