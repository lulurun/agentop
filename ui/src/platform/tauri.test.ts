/// <reference types="vite/client" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "../connections";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
const check = vi.fn();
vi.mock("@tauri-apps/plugin-updater", () => ({ check }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch: vi.fn() }));

const SERVICE = { url: "http://127.0.0.1:9775", token: "local", name: "pc" };
const respond = (service: unknown) =>
  invoke.mockImplementation(async (command: string) => (command === "read_service_info" ? service : null));
const connection = (fields: Partial<Connection>): Connection => ({ id: "x", label: "x", url: "http://127.0.0.1:9775", token: "t", ...fields });

async function platform() {
  vi.resetModules();
  return (await import("./tauri")).tauriPlatform;
}

beforeEach(() => {
  invoke.mockReset();
  check.mockReset();
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
    expect(home?.serviceToken).toBeUndefined();
  });
});

describe("the home connection", () => {
  it("is the service that served the page", async () => {
    respond(SERVICE);
    const home = await (await platform()).defaultConnection();
    expect(home?.url).toBe(location.origin);
  });
});

describe("app updates", () => {
  const INFO = { version: "0.1.0", native_api: 1, self_update: true, releases_url: "https://github.com/lulurun/agentop/releases" };
  const withInfo = (info: unknown) =>
    invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_info") {
        if (info === null) throw new Error("unknown command");
        return info;
      }
      return null;
    });

  it("has none when the updater finds none", async () => {
    withInfo(INFO);
    check.mockResolvedValue(null);
    expect(await (await platform()).checkAppUpdate!()).toBeNull();
  });

  it("installs in place where the app can replace itself", async () => {
    withInfo(INFO);
    check.mockResolvedValue({ version: "0.2.0", downloadAndInstall: vi.fn() });
    const update = await (await platform()).checkAppUpdate!();
    expect(update).toMatchObject({ version: "0.2.0", required: false, releaseUrl: "https://github.com/lulurun/agentop/releases/tag/v0.2.0" });
    expect(update?.install).toBeTypeOf("function");
  });

  it("links to the release where it can't, such as a build that isn't an AppImage", async () => {
    withInfo({ ...INFO, self_update: false });
    check.mockResolvedValue({ version: "0.2.0", downloadAndInstall: vi.fn() });
    const update = await (await platform()).checkAppUpdate!();
    expect(update?.install).toBeUndefined();
    expect(update?.releaseUrl).toContain("v0.2.0");
  });

  it("is required for an app from before desktop_info, even offline", async () => {
    withInfo(null);
    check.mockRejectedValue(new Error("offline"));
    const update = await (await platform()).checkAppUpdate!();
    expect(update).toMatchObject({ required: true, releaseUrl: "https://github.com/lulurun/agentop/releases" });
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
