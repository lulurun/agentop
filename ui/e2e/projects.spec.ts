import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

// Every service here is the test's own, with its own state and tmux server: its Coordinators are the fake agent.
const services: Service[] = [];
test.afterEach(async () => {
  await Promise.all(services.splice(0).map((s) => s.dispose()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("agentop.renderer")) localStorage.setItem("agentop.renderer", "dom");
  });
});

async function open(page: Page) {
  const s = await new Service("box").start();
  services.push(s);
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();
  const compact = page.getByRole("combobox", { name: "Feature" });
  if (await compact.isVisible()) await compact.selectOption({ label: "Projects" });
  else await page.getByRole("navigation", { name: "Features" }).getByRole("button", { name: "Projects" }).click();
  return s;
}

async function shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.AGENTOP_E2E_SHOTS;
  await page.screenshot({ path: dir ? join(dir, `${name}.png`) : info.outputPath(`${name}.png`) });
}

const projectButton = (page: Page) => page.getByRole("complementary", { name: "Left panel" }).getByRole("button", { name: "Project", exact: true });
const creator = (page: Page) => page.getByRole("region", { name: "New project", exact: true });
const creatorTabs = (page: Page) => page.getByRole("main").locator('.workbench:not([hidden]) [role="tab"]', { hasText: "New project" });

function coordinatorTool(s: Service, project: string): string | undefined {
  const records = JSON.parse(readFileSync(join(s.root, "state/projects", project, ".project/sessions.json"), "utf8")).sessions;
  return Object.values<{ display_name?: string; tool?: string }>(records).find((r) => r.display_name === "Coordinator")?.tool;
}

test("the Project button opens one creation buffer in the left area, which makes a project with the chosen Coordinator tool", async ({ page }, info) => {
  test.setTimeout(150_000);
  const s = await open(page);
  // The inline form is gone from the left panel.
  await expect(page.getByLabel("New project's name")).toHaveCount(0);

  await projectButton(page).click();
  const left = page.getByRole("region", { name: "Left area" });
  await expect(left.getByRole("region", { name: "New project", exact: true })).toBeVisible();
  const name = creator(page).getByLabel("Name");
  await expect(name).toBeFocused();
  const tool = creator(page).getByLabel("Coordinator");
  // The installed tools, codex chosen as the service would choose it.
  await expect(tool.locator("option")).toHaveText(["claude", "codex", "antigravity"]);
  await expect(tool).toHaveValue("codex");
  const create = creator(page).getByRole("button", { name: "Create", exact: true });
  await expect(create).toBeDisabled();
  await name.fill("Bad Name");
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(create).toBeDisabled();
  await name.fill("gamma");
  await expect(create).toBeEnabled();
  await shot(page, info, "desktop-buffer");

  // Clicked again it is the same tab, focused again.
  await tool.focus();
  await projectButton(page).click();
  await expect(creatorTabs(page)).toHaveCount(1);
  await expect(name).toBeFocused();
  await expect(name).toHaveValue("gamma");

  // Moved to the right area and the left one hidden: the button brings it back to the left and shows it.
  await page.getByRole("tab", { name: "New project" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the right area" }).click();
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: /status$/ }).click();
  await page.getByRole("button", { name: "Toggle left area", exact: true }).click();
  await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="left"]')).toHaveCount(0);
  await projectButton(page).click();
  await expect(left.getByRole("region", { name: "New project", exact: true })).toBeVisible();
  await expect(creatorTabs(page)).toHaveCount(1);
  await expect(name).toBeFocused();
  await expect(name).toHaveValue("gamma");

  // Busy while the request is out: nothing can be changed or sent twice.
  let release!: () => void;
  const held = new Promise<void>((r) => (release = r));
  await page.route("**/api/v2/plugins/projects/projects", async (route) => {
    if (route.request().method() === "POST") await held;
    await route.continue();
  });
  await tool.selectOption("claude");
  await name.press("Enter");
  const busy = creator(page).getByRole("button", { name: "Creating…" });
  await expect(busy).toBeDisabled();
  await expect(name).toBeDisabled();
  await expect(tool).toBeDisabled();
  await shot(page, info, "desktop-busy");
  release();

  // Made with claude, its Coordinator opens in the project.
  const list = page.getByRole("navigation", { name: "Projects" });
  await expect(list.locator('[aria-current="true"]')).toContainText("gamma", { timeout: 20_000 });
  await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 45_000 });
  expect(coordinatorTool(s, "gamma")).toBe("claude");

  // The same name again: the service's error, with what was typed kept.
  await projectButton(page).click();
  await expect(creatorTabs(page)).toHaveCount(1);
  await name.fill("gamma");
  await tool.selectOption("codex");
  await tool.press("Enter");
  await expect(creator(page).getByRole("alert")).toContainText("There is a project called gamma already");
  await expect(name).toHaveValue("gamma");
  await expect(tool).toHaveValue("codex");
  await expect(create).toBeEnabled();
  await shot(page, info, "desktop-error");
});

test("with no agent tool installed, or none listed, the buffer says so and can't create", async ({ page }) => {
  let reply: { status: number; body: string } = { status: 200, body: "[]" };
  await page.route("**/api/v2/tools", (route) => route.fulfill({ status: reply.status, contentType: "application/json", body: reply.body }));
  await open(page);
  await projectButton(page).click();
  await creator(page).getByLabel("Name").fill("delta");
  await expect(creator(page).getByRole("alert")).toContainText("No agent tool is installed");
  await expect(creator(page).getByRole("button", { name: "Create", exact: true })).toBeDisabled();
  await creator(page).getByLabel("Name").press("Enter");

  reply = { status: 500, body: JSON.stringify({ detail: "tools are down" }) };
  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await expect(creator(page).getByRole("alert")).toContainText("couldn't be listed");
  await expect(creator(page).getByRole("button", { name: "Create", exact: true })).toBeDisabled();
  await expect(page.getByRole("navigation", { name: "Projects" }).getByRole("button", { name: "delta" })).toHaveCount(0);
});

test.describe("on a narrow screen", () => {
  test.use({ viewport: { width: 420, height: 820 } });

  test("the Project button shuts the left panel over the buffer and the project is made from the keyboard", async ({ page }, info) => {
    test.setTimeout(150_000);
    const s = await open(page);
    await page.getByRole("button", { name: "Toggle left panel", exact: true }).click();
    await expect(projectButton(page)).toBeVisible();
    await projectButton(page).click();
    await expect(page.locator(".sidebar")).toHaveCount(0);
    const name = creator(page).getByLabel("Name");
    await expect(name).toBeFocused();
    await shot(page, info, "narrow-buffer");
    await page.keyboard.type("narrow-one");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 45_000 });
    expect(coordinatorTool(s, "narrow-one")).toBe("codex");
    await shot(page, info, "narrow-created");
  });
});
