import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

const services: Service[] = [];

async function service(name: string, port?: number) {
  const created = await new Service(name, port).start();
  services.push(created);
  return created;
}

test.afterEach(async () => {
  await Promise.all(services.splice(0).map((s) => s.dispose()));
});

test.beforeEach(async ({ page }) => {
  // Text can only be read from the DOM renderer. WebGL has its own test.
  await page.addInitScript(() => localStorage.setItem("agentop.renderer", "dom"));
  page.on("dialog", (dialog) => void dialog.accept());
});

const terminalText = (page: Page) => page.getByTestId("terminal").locator(".xterm-rows");

async function startSession(page: Page, s: Service, name: string, tool = "claude", cwd = join(s.root, "project")) {
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(cwd);
  await dialog.getByText(tool, { exact: true }).click();
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Start session" }).click();
}

const row = (page: Page, name: string) => page.getByRole("navigation", { name: "Sessions" }).getByRole("button", { name });

test("first visit without a token explains what to do", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(`${s.url}/app/`);
  await expect(page.getByRole("heading", { name: "Enter the token for alpha" })).toBeVisible();
  await page.getByLabel("Token").fill("wrong");
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("heading", { name: "alpha rejected the token" })).toBeVisible();
  await page.getByLabel("Token").fill(s.token);
  await page.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
});

test("the token leaves the address bar", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
  expect(page.url()).not.toContain("token");
});

test("form to working terminal, with no refresh", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(e.message));
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(row(page, "demo")).toBeVisible();
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.type("hello agent\n");
  await expect(terminalText(page)).toContainText("got: hello agent");
  expect(errors).toEqual([]);
});

test("reopening the page returns to the same session and screen", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.reload();
  await expect(terminalText(page)).toContainText("fake agent ready");
  await expect(row(page, "demo")).toHaveAttribute("aria-current", "true");
});

test("a service restart reconnects the terminal by itself", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await s.stop();
  await expect(page.getByText("Reconnecting…")).toBeVisible();
  await s.start();
  await expect(page.getByText("Reconnecting…")).toBeHidden({ timeout: 20_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.type("after restart\n");
  await expect(terminalText(page)).toContainText("got: after restart");
});

test("Ctrl+B reaches tmux", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.press("Control+b");
  await page.keyboard.press("d");
  await expect(page.getByText("Detached from tmux")).toBeVisible();
  await page.getByRole("button", { name: "Attach again" }).click();
  await expect(terminalText(page)).toContainText("fake agent ready");
});

test("collapsing a column refits the terminal", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  const session = s.tmux("list-sessions", "-F", "#S").split("\n").find((n) => n.startsWith("demo-"))!;
  const width = () => Number(s.tmux("display", "-p", "-t", `=${session}:`, "#{window_width}").trim());
  const before = width();
  await page.keyboard.press("Control+Shift+BracketRight");
  await expect.poll(width).toBeGreaterThan(before + 20);
  await page.keyboard.press("Control+Shift+BracketLeft");
  await expect.poll(width).toBeGreaterThan(before + 50);
  await page.keyboard.press("Control+Shift+BracketRight");
  await expect.poll(width).toBeLessThan(before + 50);
});

test("a failed start shows why, and what can be done", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "broken", "antigravity");
  await expect(page.getByText(/agy is not installed/)).toBeVisible({ timeout: 15_000 });
  await expect(terminalText(page)).toContainText("command not found");
  await page.getByRole("button", { name: "Dismiss" }).click();
  await expect(row(page, "broken")).toBeHidden();
});

test("an agent exit curtains the terminal and offers to close the session", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.press("Control+d");

  const curtain = page.getByRole("region", { name: "Session exited" });
  await expect(curtain).toContainText("demo has exited", { timeout: 10_000 });
  await expect(page.locator(".terminal[data-locked]")).toBeVisible();
  await expect(page.getByRole("button", { name: "Close session" })).toBeFocused();

  // The shell is still behind the curtain, but nothing typed reaches it.
  await curtain.click({ position: { x: 5, y: 5 } });
  await page.evaluate(() => document.querySelector<HTMLElement>(".xterm-helper-textarea")?.focus());
  await page.keyboard.type("echo leaked-through\n");
  await page.waitForTimeout(500);
  await expect(terminalText(page)).not.toContainText("leaked-through");

  await page.getByRole("button", { name: "Close session" }).click();
  await expect(row(page, "demo")).toBeHidden();
});

test("stop ends the session and leaves the rest of the app working", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByRole("button", { name: "Stop session" }).click();
  await expect(page.getByText("Anything typed in its input is cleared")).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(row(page, "demo")).toBeVisible();

  await page.getByRole("button", { name: "Stop session" }).click();
  await page.getByRole("button", { name: "Stop demo" }).click();
  await expect(row(page, "demo")).toBeHidden({ timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
});

async function addService(page: Page, other: Service) {
  await page.getByRole("button", { name: /Switch service|alpha|beta/ }).first().click();
  await page.getByRole("menuitem", { name: "Connect to a service" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Address").fill(other.connectInfo);
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await expect(dialog.getByText(`Found ${other.name}`)).toBeVisible();
  await dialog.getByRole("button", { name: "Add and connect" }).click();
}

test("add a second service and switch between them", async ({ page }) => {
  const alpha = await service("alpha");
  const beta = await service("beta");
  await page.goto(alpha.signedInUrl);
  await startSession(page, alpha, "on-alpha");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await page.getByTestId("terminal").click();
  await page.keyboard.type("to alpha\n");
  await expect(terminalText(page)).toContainText("got: to alpha");
  const url = page.url();

  await addService(page, beta);
  await expect(page.locator(".switcher-current")).toContainText("beta");
  expect(page.url()).toBe(url);
  await startSession(page, beta, "on-beta");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await expect(terminalText(page)).not.toContainText("got: to alpha");
  await page.getByTestId("terminal").click();
  await page.keyboard.type("to beta\n");
  await expect(terminalText(page)).toContainText("got: to beta");

  // Back to the first: same session, still running, and no reload.
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitemradio", { name: /^alpha/ }).click();
  await expect(page.locator(".switcher-current")).toContainText("alpha");
  await expect(row(page, "on-alpha")).toHaveAttribute("aria-current", "true");
  await expect(terminalText(page)).toContainText("got: to alpha");
  await expect(terminalText(page)).not.toContainText("got: to beta");

  // And on to the second again, restoring its selection.
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitemradio", { name: /beta/ }).click();
  await expect(terminalText(page)).toContainText("got: to beta");
  expect(alpha.tmux("list-sessions", "-F", "#S")).toContain("on-alpha-");
});

test("a service can be renamed in its status, and forgotten", async ({ page }) => {
  const alpha = await service("alpha");
  const beta = await service("beta");
  await page.goto(alpha.signedInUrl);
  await addService(page, beta);
  await expect(page.locator(".switcher-current")).toContainText("beta");

  // Rename the first service from its row, without switching to it.
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: "alpha status" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("my pc");
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.locator(".switcher-current")).toContainText("beta");

  // The name is kept in this browser.
  await page.reload();
  await page.locator(".switcher-current").click();
  await expect(page.getByRole("menuitemradio", { name: /^my pc/ })).toBeVisible();
  await page.getByRole("menuitemradio", { name: /^my pc/ }).click();
  await expect(page.locator(".switcher-current")).toContainText("my pc");

  // Forgetting the other one leaves only this one.
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: "beta status" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Forget" }).click();
  await page.locator(".switcher-current").click();
  await expect(page.getByRole("menuitemradio")).toHaveCount(1);
  await expect(page.getByRole("menuitem", { name: "my pc status" })).toBeVisible();
});

test("stopping the selected service leaves the app able to switch", async ({ page }) => {
  const alpha = await service("alpha");
  const beta = await service("beta");
  await page.goto(alpha.signedInUrl);
  await addService(page, beta);
  await expect(page.locator(".switcher-current")).toContainText("beta");
  await beta.stop();
  await page.reload();
  await expect(page.getByRole("heading", { name: /Can't reach beta/ })).toBeVisible();
  await page.getByRole("button", { name: "Switch service" }).click();
  await page.getByRole("menuitemradio", { name: /^alpha/ }).click();
  await expect(page.locator(".switcher-current")).toContainText("alpha");
});

test("a different service at a saved address never gets the token", async ({ page }) => {
  const alpha = await service("alpha");
  const beta = await service("beta");
  await page.goto(alpha.signedInUrl);
  await addService(page, beta);
  await expect(page.locator(".switcher-current")).toContainText("beta");
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitemradio", { name: /^alpha/ }).click();
  await expect(page.locator(".switcher-current")).toContainText("alpha");

  const port = beta.port;
  await beta.stop();
  await service("impostor", port);
  const sent: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes(`:${port}`) && request.headers().authorization) sent.push(request.url());
  });

  await page.locator(".switcher-current").click();
  await page.getByRole("menuitemradio", { name: /beta/ }).click();
  await expect(page.getByRole("heading", { name: "A different service is now at this address" })).toBeVisible();
  expect(sent).toEqual([]);
});

test("a path typed before the default directory loads is kept", async ({ page }) => {
  const s = await service("alpha");
  await page.route("**/api/v2/fs/dirs?path=%7E*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await expect(page.locator(".details").getByRole("button", { name: join(s.root, "project") })).toBeVisible();
});

test("the right panel shows details, changes, and files, and follows the selection", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  execFileSync("git", ["-C", project, "init", "-q", "-b", "trunk"]);
  mkdirSync(join(project, "src"));
  writeFileSync(join(project, "src", "inner.txt"), "x");
  writeFileSync(join(project, "notes.txt"), "x");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });

  const details = page.locator(".details");
  await expect(details.getByText("trunk")).toBeVisible();
  await expect(details.getByRole("button", { name: "notes.txt" })).toBeVisible();

  await details.getByRole("tab", { name: "All" }).click();
  await expect(details.getByRole("button", { name: "inner.txt" })).toBeHidden();
  await details.getByRole("button", { name: "src" }).click();
  await expect(details.getByRole("button", { name: "inner.txt" })).toBeVisible();

  await page.keyboard.press("Control+Shift+BracketRight");
  await expect(details).toBeHidden();
  await page.keyboard.press("Control+Shift+BracketRight");
  await expect(details).toBeVisible();
});

test("switching sessions shows one Files section, for the selected session's directory", async ({ page }) => {
  const s = await service("alpha");
  const first = join(s.root, "project");
  const second = join(s.root, "other");
  mkdirSync(second);
  writeFileSync(join(first, "first-only.txt"), "x");
  writeFileSync(join(second, "second-only.txt"), "x");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "one", "claude", first);
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
  await startSession(page, s, "two", "claude", second);
  await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });

  const details = page.locator(".details");
  for (const [name, mine, theirs] of [
    ["one", "first-only.txt", "second-only.txt"],
    ["two", "second-only.txt", "first-only.txt"],
    ["one", "first-only.txt", "second-only.txt"],
    ["two", "second-only.txt", "first-only.txt"],
  ]) {
    await row(page, name).click();
    await details.getByRole("tab", { name: "All" }).click();
    await expect(details.getByRole("button", { name: mine })).toBeVisible();
    await expect(details.getByRole("button", { name: theirs })).toHaveCount(0);
    await expect(details.getByRole("heading", { name: "Files" })).toHaveCount(1);
    await expect(details.getByRole("button", { name: "Stop session" })).toHaveCount(1);
  }
});

test("the WebGL renderer draws the terminal", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agentop.renderer", "auto"));
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await startSession(page, s, "demo");
  await expect(page.getByTestId("terminal").locator("canvas").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toHaveCount(0);
});

test("the layout fits a phone, with panels as overlays", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  await expect(page.locator(".placard")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.getByRole("button", { name: "Toggle session list" }).click();
  await expect(page.locator(".sidebar")).toBeVisible();
});

test.describe("on a touch screen", () => {
  test.use({ hasTouch: true });

  test("a swipe down on the terminal scrolls tmux back, not the page", async ({ page }) => {
    const s = await service("alpha");
    await page.goto(s.signedInUrl);
    await startSession(page, s, "demo");
    await expect(terminalText(page)).toContainText("fake agent ready", { timeout: 15_000 });
    const inMode = () => /^demo\S* 1$/m.test(s.tmux("list-panes", "-a", "-F", "#{session_name} #{pane_in_mode}"));
    expect(inMode()).toBe(false);

    const box = (await page.getByTestId("terminal").boundingBox())!;
    const x = box.x + box.width / 2;
    const cdp = await page.context().newCDPSession(page);
    const touch = (type: string, y?: number) =>
      cdp.send("Input.dispatchTouchEvent", { type, touchPoints: y === undefined ? [] : [{ x, y }] });
    await touch("touchStart", box.y + 40);
    for (let y = box.y + 60; y < box.y + 300; y += 20) await touch("touchMove", y);
    await touch("touchEnd");

    await expect.poll(inMode).toBe(true);
    expect(await page.evaluate(() => getComputedStyle(document.body).overscrollBehaviorY)).toBe("none");
  });
});
