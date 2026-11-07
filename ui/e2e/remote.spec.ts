import { expect, test, type Page } from "@playwright/test";
import { networkInterfaces } from "node:os";
import { join } from "node:path";
import { startProxy } from "./proxy";
import { Service } from "./service";

/**
 * The real setup: a service on this machine, and a devhost reached through a
 * plain nginx at http://ip:port/agentop that strips the prefix.
 */
const lanAddress = Object.values(networkInterfaces())
  .flat()
  .find((i) => i?.family === "IPv4" && !i.internal)?.address;

const cleanup: (() => unknown)[] = [];
test.afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((f) => f()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agentop.renderer", "dom"));
  page.on("dialog", (dialog) => void dialog.accept());
});

async function service(name: string) {
  const s = await new Service(name).start();
  cleanup.push(() => s.dispose());
  return s;
}

async function proxied(s: Service, options?: Parameters<typeof startProxy>[1]) {
  const proxy = await startProxy(s.port, options);
  cleanup.push(() => proxy.close());
  return proxy;
}

const terminalText = (page: Page) => page.getByTestId("terminal").locator(".xterm-rows");

async function startSession(page: Page, s: Service, name: string) {
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Start session" }).click();
}

test("the app on a local service connects to a devhost through the proxy", async ({ page }) => {
  const pc = await service("pc");
  const devhost = await service("devhost");
  const proxy = await proxied(devhost);
  await page.goto(pc.signedInUrl);
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();

  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: "Add service" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Address").fill(`http://127.0.0.1:${proxy.port}/agentop#token=${devhost.token}`);
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await expect(dialog.getByText("Found devhost")).toBeVisible();
  await dialog.getByRole("button", { name: "Add and connect" }).click();
  await expect(page.locator(".switcher-current")).toContainText("devhost");

  await startSession(page, devhost, "over-proxy");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.type("through nginx\n");
  await expect(terminalText(page)).toContainText("got: through nginx");

  await page.locator(".switcher-current").click();
  await page.getByRole("menuitemradio", { name: /This service/ }).click();
  await expect(page.locator(".switcher-current")).toContainText("pc");
});

test("a proxy that drops WebSockets is named, and the app recovers when it is fixed", async ({ page }) => {
  const pc = await service("pc");
  const devhost = await service("devhost");
  const broken = await proxied(devhost, { websockets: false });
  await page.goto(pc.signedInUrl);

  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: "Add service" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Address").fill(`http://127.0.0.1:${broken.port}/agentop#token=${devhost.token}`);
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await expect(dialog.getByText("Found devhost")).toBeVisible();
  await dialog.getByRole("button", { name: "Add and connect" }).click();
  await expect(page.getByRole("alert")).toContainText("forward WebSocket upgrades", { timeout: 15_000 });

  broken.setWebsockets(true);
  await expect(page.getByRole("alert")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
  await startSession(page, devhost, "after-the-fix");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
});

test("a rejected token can be seen and replaced, even after a reload", async ({ page }) => {
  const devhost = await service("devhost");
  const proxy = await proxied(devhost);
  const box = page.getByLabel("Token");
  await page.goto(`http://127.0.0.1:${proxy.port}/agentop/app/`);

  await expect(box).toHaveAttribute("type", "text");
  await box.fill("not-the-token");
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("heading", { name: "devhost rejected the token" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "devhost rejected the token" })).toBeVisible();
  await box.fill(devhost.token);
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
});

test("the old routes cannot be reached through the proxy", async ({ request }) => {
  const devhost = await service("devhost");
  const proxy = await proxied(devhost);
  const base = `http://127.0.0.1:${proxy.port}/agentop`;
  expect((await request.get(`${base}/api/sessions`)).status()).toBe(403);
  expect((await request.post(`${base}/api/sessions/start`, { data: {} })).status()).toBe(403);
  expect((await request.get(`${base}/api/v2/sessions`)).status()).toBe(401);
  expect((await request.get(`${base}/api/v2/health`)).status()).toBe(200);
  // Directly, on the machine itself, they still work.
  expect((await request.get(`${devhost.url}/api/sessions`)).status()).toBe(200);
});

test("the old address through the proxy leads to the app", async ({ page }) => {
  const devhost = await service("devhost");
  const proxy = await proxied(devhost);
  await page.goto(`http://127.0.0.1:${proxy.port}/agentop/`);
  await expect(page).toHaveURL(/\/agentop\/app\/$/);
  await expect(page.getByRole("heading", { name: "Enter the token for devhost" })).toBeVisible();
});

test("the app loaded through the proxy over plain HTTP works, including Add service", async ({ page }) => {
  test.skip(!lanAddress, "needs a non-loopback address");
  const devhost = await service("devhost");
  const other = await service("other");
  const proxy = await proxied(devhost);
  const otherProxy = await proxied(other);

  await page.goto(`http://${lanAddress}:${proxy.port}/agentop/app/#token=${devhost.token}`);
  expect(await page.evaluate(() => isSecureContext)).toBe(false);
  await startSession(page, devhost, "plain-http");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });

  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: "Add service" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Address").fill(`http://${lanAddress}:${otherProxy.port}/agentop#token=${other.token}`);
  await expect(dialog.getByText(/Not encrypted/)).toBeVisible();
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await expect(dialog.getByText("Found other")).toBeVisible();
  await dialog.getByRole("button", { name: "Add and connect" }).click();
  await expect(page.locator(".switcher-current")).toContainText("other");
});
