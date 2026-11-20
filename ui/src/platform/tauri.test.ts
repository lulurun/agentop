/// <reference types="vite/client" />
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "../connections";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

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
  const INFO = { version: "2.0.0", native_api: 2, releases_url: "https://github.com/lulurun/agentop/releases", service_version: "2.0.0" };
  const withInfo = (info: unknown) =>
    invoke.mockImplementation(async (command: string) => {
      if (command === "desktop_info") {
        if (info === null) throw new Error("unknown command");
        return info;
      }
      return null;
    });

  it("has none when the app and its agentop are the newest", async () => {
    withInfo(INFO);
    expect(await (await platform()).checkAppUpdate!("2.0.0")).toBeNull();
    expect(await (await platform()).checkAppUpdate!(null)).toBeNull();
  });

  it("updates this computer's agentop in place for a new minor or patch release", async () => {
    withInfo(INFO);
    for (const version of ["2.0.1", "2.1.0"]) {
      const update = await (await platform()).checkAppUpdate!(version);
      expect(update).toMatchObject({ kind: "service", version, required: false });
      await update!.install!();
      expect(invoke).toHaveBeenCalledWith("update_open", undefined);
    }
  });

  it("updates an agentop older than the app itself, as after a new app is installed", async () => {
    withInfo({ ...INFO, service_version: "1.3.0" });
    expect(await (await platform()).checkAppUpdate!(null)).toMatchObject({ kind: "service", version: "2.0.0" });
  });

  it("links to the release page for a new major version", async () => {
    withInfo(INFO);
    const update = await (await platform()).checkAppUpdate!("3.0.2");
    expect(update).toMatchObject({ kind: "app", version: "3.0.2", releaseUrl: "https://github.com/lulurun/agentop/releases/latest" });
    expect(update?.install).toBeUndefined();
  });

  it("is required for an app without the update command, even offline", async () => {
    withInfo({ version: "1.2.0", native_api: 1, releases_url: "https://github.com/lulurun/agentop/releases" });
    expect(await (await platform()).checkAppUpdate!(null)).toMatchObject({ kind: "app", required: true });
    withInfo(null);
    expect(await (await platform()).checkAppUpdate!(null)).toMatchObject({ kind: "app", required: true });
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
