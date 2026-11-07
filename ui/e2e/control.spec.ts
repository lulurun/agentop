import { expect, test, type Page } from "@playwright/test";
import { join } from "node:path";
import { Service } from "./service";

const cleanup: (() => unknown)[] = [];
test.afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((f) => f()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agentop.renderer", "dom"));
  page.on("dialog", (dialog) => void dialog.accept());
});

async function open(page: Page, s: Service) {
  cleanup.push(() => s.dispose());
  await s.start();
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();
}

async function startSession(page: Page, s: Service, name: string) {
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Start session" }).click();
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
}

async function openPanel(page: Page) {
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: /status$/ }).click();
  return page.getByRole("dialog");
}

/** Report one new commit, whatever the machine's real git state is. */
async function pretendBehind(page: Page) {
  await page.route("**/api/v2/service/update", async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), check_error: null, behind: 1 } });
  });
}

test("a session's name and description can be edited in place", async ({ page }) => {
  const s = new Service("box");
  await open(page, s);
  await startSession(page, s, "first");

  const details = page.locator(".details");
  await details.getByRole("button", { name: "first", exact: true }).click();
  await details.getByLabel("Name").fill("renamed");
  await page.keyboard.press("Enter");
  await expect(page.locator(".sidebar")).toContainText("renamed");

  await details.getByRole("button", { name: "Add a description" }).click();
  await details.getByLabel("Description").fill("fixing the login bug");
  await page.keyboard.press("Enter");
  await expect(details.getByRole("button", { name: "fixing the login bug" })).toBeVisible();

  await details.getByRole("button", { name: "fixing the login bug" }).click();
  await details.getByLabel("Description").fill("not this");
  await page.keyboard.press("Escape");
  await expect(details.getByRole("button", { name: "fixing the login bug" })).toBeVisible();

  await page.reload();
  await expect(page.locator(".sidebar")).toContainText("renamed");
});

test("a service started by hand explains why it can't be controlled", async ({ page }) => {
  const s = new Service("by-hand");
  await open(page, s);
  const panel = await openPanel(page);
  await expect(panel).toContainText("started by hand");
  for (const name of ["Stop", "Restart", "Update"]) {
    await expect(panel.getByRole("button", { name, exact: true })).toBeDisabled();
  }
});

test("restart names the service, keeps the session, and reconnects by itself", async ({ page }) => {
  const s = new Service("devbox").managed();
  await open(page, s);
  await startSession(page, s, "keeps-running");

  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "Restart", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Restart devbox?" })).toBeVisible();
  await panel.getByRole("button", { name: "Restart service" }).click();
  await expect.poll(() => s.requested("restart")).toBe(true);

  await s.stop();
  await s.start();
  await panel.getByRole("button", { name: "Close" }).click();
  await expect(page.locator(".link-connected")).toBeVisible({ timeout: 15_000 });

  await page.getByTestId("terminal").click();
  await page.keyboard.type("still here\n");
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("got: still here", { timeout: 15_000 });
});

test("stop warns, shows a stopped state, and comes back when started again", async ({ page }) => {
  const s = new Service("devbox").managed();
  await open(page, s);
  await startSession(page, s, "survivor");

  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Stop devbox?" })).toBeVisible();
  await expect(panel).toContainText("can't start the service again");
  await panel.getByRole("button", { name: "Stop service" }).click();

  await expect(page.getByRole("heading", { name: /is stopped$/ })).toBeVisible();
  await expect(page.getByText("systemctl --user start agentop-api")).toBeVisible();
  await expect.poll(() => s.requested("stop")).toBe(true);

  await s.stop();
  await s.start();
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.locator(".sidebar")).toContainText("survivor");
});

test("update shows its steps and then the result", async ({ page }) => {
  const s = new Service("devbox").managed();
  await open(page, s);
  await pretendBehind(page);

  const panel = await openPanel(page);
  await expect(panel).toContainText("1 new commit");
  await panel.getByRole("button", { name: "Update", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Update devbox?" })).toBeVisible();
  await panel.getByRole("button", { name: "Update service" }).click();

  s.reportUpdate("running", { step: "building" });
  await expect(panel.getByRole("status")).toContainText("Building the app", { timeout: 6_000 });
  await expect(panel.getByRole("button", { name: "Restart", exact: true })).toBeDisabled();
  s.reportUpdate("done", { to_commit: "abc1234" });
  await expect(panel.getByRole("status")).toContainText("Updated to abc1234", { timeout: 6_000 });
});

test("a failed update says it went back", async ({ page }) => {
  const s = new Service("devbox").managed();
  await open(page, s);
  await pretendBehind(page);

  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "Update", exact: true }).click();
  await panel.getByRole("button", { name: "Update service" }).click();
  s.reportUpdate("running", { step: "verifying" });
  await expect(panel.getByRole("status")).toContainText("Checking that it started", { timeout: 6_000 });
  s.reportUpdate("rolled_back", { message: "The new version failed to install or start. The service is back on the previous version." });
  await expect(panel.getByRole("alert")).toContainText("back on the previous version", { timeout: 6_000 });
});
