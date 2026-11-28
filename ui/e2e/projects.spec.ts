import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

const projectButton = (page: Page) => page.getByRole("complementary", { name: "Left panel" }).getByRole("button", { name: "New project", exact: true });
const areaTabs = (page: Page, area: "Left" | "Right") =>
  page.getByRole("main").locator(".workbench:not([hidden])").getByRole("tablist", { name: `${area} area tabs` }).getByRole("tab");
const creator = (page: Page) => page.getByRole("region", { name: "New project", exact: true });
const creatorTabs = (page: Page) => page.getByRole("main").locator('.workbench:not([hidden]) [role="tab"]', { hasText: "New project" });

function coordinatorTool(s: Service, project: string): string | undefined {
  const records = JSON.parse(readFileSync(join(s.root, "state/projects", project, ".project/sessions.json"), "utf8")).sessions;
  return Object.values<{ display_name?: string; tool?: string }>(records).find((r) => r.display_name === "Coordinator")?.tool;
}

test("the New project button opens one creation buffer in the left area, which makes a project with the chosen Coordinator tool", async ({ page }, info) => {
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

  // Made with claude, its Coordinator opens in the project's own Coordinator tab, beside its Progress tab.
  const list = page.getByRole("navigation", { name: "Projects" });
  await expect(list.locator('[aria-current="true"]')).toContainText("gamma", { timeout: 20_000 });
  await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 45_000 });
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: gamma"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: gamma"]);
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

  test("the New project button shuts the left panel over the buffer and the project is made from the keyboard", async ({ page }, info) => {
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
    // One area at a time: the Coordinator shows, and the right area's toggle switches to Progress and back.
    const groups = page.locator('.workbench:not([hidden]) .tab-group');
    await expect(groups.locator(":visible").first()).toBeVisible();
    await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="left"]')).toBeVisible();
    await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="right"]')).toBeHidden();
    await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: narrow-one"]);
    await page.getByRole("button", { name: "Toggle right area", exact: true }).click();
    await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="right"]')).toBeVisible();
    await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="left"]')).toBeHidden();
    await expect(areaTabs(page, "Right")).toHaveText(["Progress: narrow-one"]);
    await expect(page.locator(".progress-viewer")).toBeVisible();
    await shot(page, info, "narrow-progress");
    await page.getByRole("button", { name: "Toggle left area", exact: true }).click();
    await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="left"]')).toBeVisible();
    // A reload shows the project again with its Coordinator.
    await page.reload();
    await expect(page.locator(".link-connected")).toBeVisible();
    await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: narrow-one"]);
    await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 20_000 });
    await expect(page.locator(".workbench:not([hidden]) .tab-close")).toHaveCount(0);
    await shot(page, info, "narrow-reloaded");
  });
});

test("the Progress Viewer lists each action's results beneath it and opens a text result in a file tab", async ({ page }, info) => {
  test.setTimeout(150_000);
  const s = await open(page);
  await projectButton(page).click();
  await creator(page).getByLabel("Name").fill("results");
  const create = creator(page).getByRole("button", { name: "Create", exact: true });
  await expect(create).toBeEnabled();
  await create.click();
  await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 45_000 });

  const folder = join(s.root, "state/projects/results");
  writeFileSync(join(folder, "track/1/PLAN.md"),
    "# Plan 1\n\n## M1 Build  [in progress]\n\n- M1A1 Write the report  [done] → P1_M1A1-1\n- M1A10 Tenth action  [planned]\n");
  mkdirSync(join(folder, "results"), { recursive: true });
  writeFileSync(join(folder, "results/P1_M1A1_report.md"), "# Report of M1A1\n\nAll 12 checks pass.\n");
  writeFileSync(join(folder, "results/P1_M1A1_tests.txt"), "12 passed\n");
  writeFileSync(join(folder, "results/P1_M1A10_other.md"), "tenth\n");
  writeFileSync(join(folder, "results/P2_M1A1_next.md"), "next direction\n");
  writeFileSync(join(folder, "results/old-report.md"), "older artifact\n");
  // An older task, from before prefixes: its record names the artifact.
  writeFileSync(join(folder, ".project/tasks.json"), JSON.stringify({ tasks: [{
    id: "P1_M1A1-1", action: "M1A1", direction: 1, session: "dev", tool: "codex", goal: "Write it", accept: ["passes"],
    artifact: "results/old-report.md", status: "accepted", opened_at: 1_700_000_000, closed_at: 1_700_000_100,
    rounds: [{ sent_at: 1_700_000_000, reported_at: 1_700_000_050, summary: "done" }], checked: "read it",
  }] }));

  // The Progress Viewer button shows the project's one Progress tab, adding none.
  await page.getByRole("complementary", { name: "Right panel" }).getByRole("button", { name: "Progress Viewer" }).click();
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: results"]);
  await page.getByRole("button", { name: "Refresh" }).click();
  const viewer = page.locator(".progress-viewer");
  const m1a1 = viewer.getByRole("list", { name: "Results of M1A1", exact: true });
  await expect(m1a1.getByRole("listitem")).toHaveText([
    /^old-report\.mdartifact of P1_M1A1-1/,
    /^P1_M1A1_report\.mdsupporting/,
    /^P1_M1A1_tests\.txtsupporting/,
  ]);
  await expect(viewer.getByRole("list", { name: "Results of M1A10", exact: true }).getByRole("listitem")).toHaveText([/^P1_M1A10_other\.md/]);
  await expect(viewer.getByText("P2_M1A1_next.md")).toHaveCount(0);
  await m1a1.scrollIntoViewIfNeeded();
  await shot(page, info, "results-by-action");

  const asked = page.waitForRequest((r) => r.url().includes("/plugins/viewer/file?"));
  await m1a1.getByRole("button", { name: "P1_M1A1_tests.txt" }).click();
  const request = await asked;
  expect(new URL(request.url()).searchParams.get("project")).toBe("results");
  const text = page.getByLabel("Contents of results/P1_M1A1_tests.txt");
  await expect(text).toHaveText("12 passed\n");
  await expect(page.getByRole("tab", { name: /P1_M1A1_tests\.txt/ })).toBeVisible();
  await m1a1.getByRole("button", { name: "P1_M1A1_report.md" }).click();
  await expect(page.getByLabel("Preview of results/P1_M1A1_report.md")).toContainText("All 12 checks pass.");
  // Opened beside the viewer, after the Coordinator tab, which stays.
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: results", "P1_M1A1_tests.txt", "P1_M1A1_report.md"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: results"]);
  await shot(page, info, "result-opened");

  // The service refuses a path out of the project, whatever the page asks.
  const headers = await request.allHeaders();
  const read = (path: string) => page.request.get(`${s.url}/api/v2/plugins/viewer/file?${new URLSearchParams({ project: "results", source: "project", path })}`, { headers });
  expect((await read("results/P1_M1A1_tests.txt")).status()).toBe(200);
  expect((await read("../../service.json")).status()).toBe(403);
  expect((await read(".project/tasks.json")).status()).toBe(403);
});

test("every project keeps a Coordinator tab on the left and a Progress tab on the right that can't be closed or moved", async ({ page }, info) => {
  test.setTimeout(150_000);
  const s = await open(page);
  const coordinatorPosts: string[] = [];
  page.on("request", (request) => {
    // Only what would start a process: a session, a Coordinator, or a resume. Project creation is the test's own.
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && (path === "/api/v2/sessions" || /\/(coordinator|resume)$/.test(path))) coordinatorPosts.push(request.url());
  });
  const left = page.getByRole("complementary", { name: "Left panel" });
  const list = page.getByRole("navigation", { name: "Projects" });
  const make = async (name: string) => {
    await projectButton(page).click();
    await creator(page).getByLabel("Name").fill(name);
    await creator(page).getByRole("button", { name: "Create", exact: true }).click();
    await expect(list.locator('[aria-current="true"]')).toContainText(name, { timeout: 20_000 });
  };
  await make("alpha");
  await expect(page.locator('[data-testid="terminal"]:visible')).toContainText("fake agent ready", { timeout: 45_000 });
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);
  await expect(page.locator(".progress-viewer")).toBeVisible();
  // The Coordinator tab selects its session for Details.
  await expect(page.getByRole("complementary", { name: "Right panel" }).getByRole("button", { name: "Progress Viewer" })).toBeVisible();
  await shot(page, info, "desktop-permanent-tabs");

  // No close button, middle click, menu or close key takes either away.
  const coordinator = areaTabs(page, "Left").first();
  const progress = areaTabs(page, "Right").first();
  await expect(page.locator(".workbench:not([hidden]) .tab-close")).toHaveCount(0);
  for (const tab of [coordinator, progress]) {
    await tab.click({ button: "middle" });
    await tab.click({ button: "right" });
    await expect(page.getByRole("menu")).toHaveCount(0);
    await tab.click();
    await page.keyboard.press("Control+Shift+KeyL");
  }
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);

  // An ordinary tab beside them still closes and moves.
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: /status$/ }).click();
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha", "Status"]);
  await areaTabs(page, "Left").nth(1).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the right area" }).click();
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha", "Status"]);
  await areaTabs(page, "Right").nth(1).click({ button: "middle" });
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);

  // Opening the Coordinator from the session list shows it in its tab, not a second one.
  await left.getByRole("navigation", { name: "Sessions", exact: true }).getByText("Coordinator").click();
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);

  // The area toggles work as before.
  await page.getByRole("button", { name: "Toggle right area", exact: true }).click();
  await expect(page.locator('.workbench:not([hidden]) .tab-group[data-area="right"]')).toHaveCount(0);
  await page.getByRole("button", { name: "Toggle right area", exact: true }).click();
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);

  // Another project has its own pair; going back shows alpha's, once each.
  await make("beta");
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: beta"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: beta"]);
  await left.getByRole("button", { name: "alpha", exact: true }).click();
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);
  // Shut, then entered again: both areas show.
  await page.getByRole("button", { name: "Toggle right area", exact: true }).click();
  await left.getByRole("button", { name: "beta", exact: true }).click();
  await left.getByRole("button", { name: "alpha", exact: true }).click();
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);

  // Its Coordinator gone: the tab stays, and says so; nothing starts another.
  const records = JSON.parse(readFileSync(join(s.root, "state/projects/alpha/.project/sessions.json"), "utf8")).sessions;
  const alphaId = Object.keys(records).find((id) => records[id].display_name === "Coordinator")!;
  s.tmux("kill-session", "-t", alphaId);
  await expect(page.getByRole("heading", { name: "No Coordinator is running in alpha" })).toBeVisible({ timeout: 15_000 });
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);
  await shot(page, info, "desktop-no-coordinator");

  // Reloaded, the same tabs come back, once each, and the saved file has them.
  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: alpha"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: alpha"]);
  await expect(page.getByRole("heading", { name: "No Coordinator is running in alpha" })).toBeVisible();
  await left.getByRole("button", { name: "beta", exact: true }).click();
  await expect(page.locator('[data-testid="terminal"]:visible')).toContainText("fake agent ready", { timeout: 20_000 });
  const saved = JSON.parse(readFileSync(join(s.root, "state/projects/alpha/.project/layout.json"), "utf8"));
  expect(saved["mid-panel"].left.tabs.map((t: { content: { view?: string } }) => t.content.view)).toEqual(["coordinator"]);
  expect(saved["mid-panel"].right.tabs.map((t: { content: { view?: string } }) => t.content.view)).toEqual(["progress"]);
  expect(Object.keys(JSON.parse(readFileSync(join(s.root, "state/projects/alpha/.project/sessions.json"), "utf8")).sessions)).toEqual(Object.keys(records));
  expect(coordinatorPosts).toEqual([]);
});

test("a project saved before keeps its own tabs and sizes, and gains the two", async ({ page }, info) => {
  const s = await open(page);
  const directory = join(s.root, "state/projects/legacy");
  mkdirSync(join(directory, ".project"), { recursive: true });
  writeFileSync(join(directory, ".project", "project.json"), JSON.stringify({ owner: "projects", created_at: 0 }));
  const file = join(directory, ".project", "layout.json");
  const progress = { kind: "plugin", plugin: "projects", view: "progress", key: "legacy" };
  writeFileSync(file, JSON.stringify({
    version: 1,
    "left-panel": { open: true, width: 300 },
    "mid-panel": {
      left: { open: true, tabs: [{ id: "x", content: { kind: "file", file: { sessionId: "legacy", source: "project", path: "notes.md" } } }, { id: "p", content: progress }], active: "x" },
      right: { open: false, tabs: [{ id: "s", content: { kind: "status" } }], active: "s" },
      split: 0.4,
    },
    "right-panel": { open: true, width: 360 },
    focused: "left",
  }));
  writeFileSync(join(directory, "notes.md"), "# Notes\n");
  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await page.getByRole("complementary", { name: "Left panel" }).getByRole("button", { name: "legacy", exact: true }).click();
  await expect(areaTabs(page, "Left")).toHaveText(["Coordinator: legacy", "notes.md"]);
  await expect(areaTabs(page, "Right")).toHaveText(["Progress: legacy", "Status"]);
  await expect(page.getByRole("heading", { name: "No Coordinator is running in legacy" })).toHaveCount(0);
  await expect(page.getByLabel("Preview of notes.md")).toBeVisible();
  const box = await page.locator(".sidebar").boundingBox();
  expect(Math.round(box!.width)).toBe(300);
  await shot(page, info, "desktop-legacy");
  // Saved on the next change, with nothing of its own lost.
  await areaTabs(page, "Left").first().click();
  await expect(page.getByRole("heading", { name: "No Coordinator is running in legacy" })).toBeVisible();
  await expect.poll(() => JSON.parse(readFileSync(file, "utf8"))["mid-panel"].left.tabs.map((t: { content: { kind: string } }) => t.content.kind)).toEqual(["plugin", "file"]);
  const saved = JSON.parse(readFileSync(file, "utf8"));
  expect(saved["mid-panel"].right.tabs.map((t: { content: { kind: string } }) => t.content.kind)).toEqual(["plugin", "status"]);
  expect(saved["mid-panel"].split).toBe(0.4);
  expect(saved["left-panel"]).toEqual({ open: true, width: 300 });
});
