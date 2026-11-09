import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, ServiceClient } from "./client";

function serve(routes: Record<string, number>) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname.replace("/api/v2", "");
      calls.push(`${init.method} ${path}`);
      const status = routes[`${init.method} ${path}`] ?? 404;
      return new Response(JSON.stringify(status < 400 ? { status: "ok" } : { detail: "no" }), { status });
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("admin actions", () => {
  const client = new ServiceClient("http://box:9775", "t");

  it("go to /admin", async () => {
    const calls = serve({ "POST /admin/restart": 202 });
    await client.restartService();
    expect(calls).toEqual(["POST /admin/restart"]);
  });

  it("fall back to /service on a service from before /admin, so it can still be updated", async () => {
    const calls = serve({ "POST /service/update": 202, "GET /service/update": 200 });
    await client.startUpdate();
    await client.updateInfo();
    expect(calls).toEqual(["POST /admin/update", "POST /service/update", "GET /admin/update", "GET /service/update"]);
  });

  it("don't fall back when the service refuses", async () => {
    const calls = serve({ "POST /admin/stop": 409 });
    await expect(client.stopService()).rejects.toBeInstanceOf(ApiError);
    expect(calls).toEqual(["POST /admin/stop"]);
  });
});
