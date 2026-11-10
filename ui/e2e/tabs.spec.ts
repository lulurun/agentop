import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

const services: Service[] = [];

async function service(name: string) {
  const created = await new Service(name).start();
  services.push(created);
  return created;
}

test.afterEach(async () => {
  await Promise.all(services.splice(0).map((s) => s.dispose()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agentop.renderer", "dom"));
});

async function startSession(page: Page, name: string, cwd: string) {
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(cwd);
  await dialog.getByText("claude", { exact: true }).click();
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Start session" }).click();
  await expect(page.getByRole("tab", { name, selected: true })).toBeVisible();
  await expect(page.getByTestId("terminal").first().locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
}

const row = (page: Page, name: string) => page.getByRole("navigation", { name: "Sessions" }).getByRole("button", { name });

test("clicking a session switches the tab, and a new tab or the side keeps both", async ({ page }) => {
  const s = await service("alpha");
  const other = join(s.root, "other");
  mkdirSync(other);
  await page.goto(s.signedInUrl);
  await startSession(page, "one", join(s.root, "project"));
  await startSession(page, "two", other);

  const main = page.locator(".main");
  // Starting "two" took the place of "one".
  await expect(main.getByRole("tab")).toHaveText(["two"]);

  await row(page, "one").click({ modifiers: ["ControlOrMeta"] });
  await expect(main.getByRole("tab")).toHaveText(["two", "one"]);
  await expect(main.getByRole("tab", { name: "one" })).toHaveAttribute("aria-selected", "true");

  await row(page, "two").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Open to the side" }).click();
  await expect(page.getByRole("region", { name: "Right group" }).getByRole("tab")).toHaveText(["two"]);
  await expect(page.getByTestId("terminal")).toHaveCount(2);

  // The right panel follows the focused group.
  await page.getByRole("region", { name: "Left group" }).getByRole("tab", { name: "one" }).click();
  await expect(page.locator(".header-title strong")).toHaveText("one");

  await page.getByRole("region", { name: "Right group" }).getByRole("button", { name: "Close two" }).click();
  await expect(page.getByRole("region", { name: "Right group" })).toHaveCount(0);
  await expect(main.getByRole("tab")).toHaveText(["two", "one"]);

  await page.reload();
  await expect(page.locator(".main").getByRole("tab")).toHaveText(["two", "one"]);
});

test("a file from the right panel opens in a tab", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  mkdirSync(join(project, "src"), { recursive: true });
  writeFileSync(join(project, "src", "hello.py"), "print('hello')\nprint('again')\n");
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  const details = page.locator(".details");
  await details.getByRole("tab", { name: "All" }).click();
  await details.getByRole("button", { name: "src" }).click();
  await details.getByRole("button", { name: "hello.py" }).click();

  const main = page.locator(".main");
  await expect(main.getByRole("tab")).toHaveText(["demo", "hello.py"]);
  await expect(main.getByLabel("Contents of src/hello.py")).toHaveText("print('hello')\nprint('again')\n");
  await expect(main.getByText("2 lines")).toBeVisible();

  // Moved beside the terminal, the file keeps focus: the terminal reopening on the left doesn't take it.
  await main.getByRole("tab", { name: "hello.py" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the right" }).click();
  await expect(page.getByTestId("terminal")).toBeVisible();
  await expect(page.getByRole("region", { name: "Right group" })).toHaveAttribute("data-focused", "true");
  await expect(page.getByRole("region", { name: "Right group" }).getByLabel("Contents of src/hello.py")).toBeVisible();
});
