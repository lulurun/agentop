import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

const cleanup: (() => unknown)[] = [];
test.afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((f) => f()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("agentop.renderer")) localStorage.setItem("agentop.renderer", "dom");
  });
  page.on("dialog", (dialog) => void dialog.accept());
});

async function open(page: Page, name = "box") {
  const s = await new Service(name).start();
  cleanup.push(() => s.dispose());
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();
  return s;
}

function seedDialogue(s: Service, name: string, session = "diaabcdea-1", status = "completed") {
  const directory = join(s.root, "state/plugins/dialogue/dialogues", name);
  mkdirSync(directory, { recursive: true });
  writeFileSync(
    join(directory, "meta.json"),
    JSON.stringify({
      id: name.split("_").pop(),
      session_a: session,
      session_b: "diaabcdeb-1",
      agent_a: "claude",
      agent_b: "codex",
      max_turns: 10,
      start_time: "2026-11-01T00:00:00+00:00",
      status,
      scenario_name: "debate",
      brief_name: "topic",
    }),
  );
  writeFileSync(join(directory, "brief.md"), "Decide the plan.");
  writeFileSync(join(directory, "progress.md"), "They agreed.");
  return directory;
}

async function openStatus(page: Page, name = "box") {
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: `${name} status` }).click();
  return page.getByRole("region", { name: "Status" });
}

const workspaces = (page: Page) => page.getByRole("navigation", { name: "Workspaces" });

test("history is a workspace: searched, filtered, sorted, resumed, and deleted", async ({ page }) => {
  const s = await open(page);
  let saved = [
    { session_id: "aaa", tool: "claude", title: "Fix login", cwd: join(s.root, "project"), last_active: 1790000000 },
    { session_id: "bbb", tool: "codex", title: "Write docs", cwd: join(s.root, "project"), last_active: 1790000100 },
    { session_id: "ccc", tool: "claude", title: "Old idea", cwd: join(s.root, "project"), last_active: 1780000000 },
  ];
  await page.route("**/api/v2/saved-sessions?*", (route) => route.fulfill({ json: saved }));
  await page.route("**/api/v2/saved-sessions/*/*", (route) => {
    saved = saved.filter((item) => !route.request().url().endsWith(item.session_id));
    return route.fulfill({ json: { ok: true } });
  });

  await workspaces(page).getByRole("button", { name: "History" }).click();
  await expect(workspaces(page).getByRole("button", { name: "History" })).toHaveAttribute("aria-current", "page");
  // History has no left panel, and its list is a pinned tab.
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Toggle left panel" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "History" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close History" })).toHaveCount(0);

  const view = page.getByRole("region", { name: "History" });
  const titles = view.getByRole("list", { name: "Past sessions" }).locator(".view-row-title");
  await expect(titles).toHaveText(["Write docs", "Fix login", "Old idea"]);
  await view.getByLabel("Sort by").selectOption("tool");
  await expect(titles).toHaveText(["Fix login", "Old idea", "Write docs"]);
  await view.getByLabel("CLI").selectOption("codex");
  await expect(titles).toHaveText(["Write docs"]);
  await view.getByLabel("CLI").selectOption("");
  await view.getByLabel("Search history").fill("login");
  await expect(titles).toHaveText(["Fix login"]);
  await view.getByLabel("Search history").fill("");

  const details = page.getByRole("complementary", { name: "Past session" });
  await view.getByRole("button", { name: /Write docs/ }).click();
  await expect(details).toContainText("bbb");
  await details.getByRole("button", { name: "Delete" }).click();
  await details.getByRole("button", { name: "Keep" }).click();
  await expect(view).toContainText("Write docs");
  await details.getByRole("button", { name: "Delete" }).click();
  await details.getByRole("button", { name: "Delete for good" }).click();
  await expect(view).not.toContainText("Write docs");
  await expect(page.getByText("Select a past session to see its details.")).toBeVisible();

  await view.getByRole("button", { name: /Fix login/ }).click();
  await page.getByRole("complementary", { name: "Past session" }).getByRole("button", { name: "Resume" }).click();
  // Resuming lands in Sessions.
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
  await expect(workspaces(page).getByRole("button", { name: "Sessions" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("region", { name: "History" })).toBeHidden();
});

test("an empty history says so", async ({ page }) => {
  await open(page);
  await workspaces(page).getByRole("button", { name: "History" }).click();
  await expect(page.getByText("No past sessions on this service.")).toBeVisible();
});

test("each workspace keeps its tabs, and Sessions keeps its terminal connected", async ({ page }) => {
  const s = await open(page);
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill("keeper");
  await dialog.getByRole("button", { name: "Start session" }).click();
  const terminal = page.getByTestId("terminal");
  await expect(terminal.locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });

  await page.keyboard.press("Control+Shift+Digit2");
  await expect(page.getByRole("region", { name: "History" })).toBeVisible();
  // Status opens in this workspace, on the left.
  await openStatus(page);
  await expect(page.getByRole("tab", { name: "Status" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["History", "Status"]);

  await page.keyboard.press("Control+Shift+Digit1");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["keeper"]);
  await terminal.click();
  await page.keyboard.type("still connected\n");
  await expect(terminal.locator(".xterm-rows")).toContainText("got: still connected", { timeout: 15_000 });

  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await workspaces(page).getByRole("button", { name: "History" }).click();
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["History", "Status"]);
});

test("a finished dialogue shows each agent's messages side by side, its stats and artifacts, and can be removed", async ({ page }) => {
  const s = await open(page);
  const directory = seedDialogue(s, "debate_topic_abcde123");
  writeFileSync(join(directory, "scenario.toml"), 'name = "debate"\n[role_a]\nname = "Pro"\nprompt = "x"\n[role_b]\nname = "Con"\nprompt = "y"\n');
  const turns = [
    { turn: 1, side: "a", name: "Pro", status: "continue", body: "We should **ship** it.", at: "2026-11-01T00:01:00+00:00" },
    { turn: 2, side: "b", name: "Con", status: "continue", body: "Not before the tests.", at: "2026-11-01T00:02:00+00:00" },
    { turn: 3, side: "a", name: "Pro", status: "complete", body: "Agreed: tests first.", at: "2026-11-01T00:03:00+00:00" },
  ];
  writeFileSync(join(directory, "turns.jsonl"), turns.map((t) => JSON.stringify(t)).join("\n") + "\n");
  mkdirSync(join(directory, "deliverables"));
  writeFileSync(join(directory, "deliverables/plan.md"), "# The plan\n\nTests first.");

  await workspaces(page).getByRole("button", { name: "Dialogues" }).click();
  // No tabs to start with: a placard, as in Sessions, and the left panel is only the list.
  await expect(page.locator(".main .placard")).toContainText("Create a new dialogue, or pick one from the list");
  await expect(page.locator('.workbench[data-workspace="plugin:dialogue"]')).toHaveCount(0);
  await expect(page.locator(".sidebar").getByRole("button", { name: "New dialogue" })).toHaveCount(0);
  const list = page.locator(".sidebar").getByRole("navigation", { name: "Dialogues" });
  await list.getByRole("button", { name: /Decide the plan/ }).click();

  const left = page.getByRole("region", { name: "Left group" });
  const right = page.getByRole("region", { name: "Right group" });
  await expect(left.locator(".dialogue-message")).toHaveCount(2);
  await expect(left.locator(".dialogue-message strong", { hasText: "ship" })).toBeVisible();
  await expect(left).toContainText("Marked the dialogue complete");
  await expect(right.locator(".dialogue-message")).toHaveText([/Not before the tests/]);

  const details = page.locator(".details");
  await expect(details).toContainText("3 of 10");
  await expect(details).toContainText("Pro marked it complete");
  await details.getByRole("button", { name: /plan\.md/ }).click();
  await expect(right.getByRole("heading", { name: "The plan" })).toBeVisible();

  await details.getByRole("button", { name: "Remove dialogue…" }).click();
  await details.getByRole("button", { name: "Remove for good" }).click();
  await expect(list).toContainText("None yet.");
  await expect(page.getByRole("region", { name: "Right group" })).toHaveCount(0);
});

test("the New dialogue wizard asks for every field, then starts two agents watched read-only and kept out of the session list", async ({ page }) => {
  const s = await open(page);
  await workspaces(page).getByRole("button", { name: "Dialogues" }).click();
  await page.locator(".header").getByRole("button", { name: "New dialogue" }).click();

  const wizard = page.getByRole("region", { name: "New dialogue" });
  await wizard.getByLabel("Scenario").selectOption("shelf/pm-sde");
  const start = wizard.getByRole("button", { name: "Start dialogue" });
  await expect(start).toBeDisabled();
  await wizard.getByLabel("Title").fill("Ship the widget");
  await expect(start).toBeDisabled();
  await wizard.getByLabel("Brief text").fill("Build the widget, with tests.");
  await expect(start).toBeEnabled();
  await start.click();

  await expect(wizard).toHaveCount(0);
  const details = page.locator(".details");
  await expect(details).toContainText("Ship the widget");
  const left = page.getByRole("region", { name: "Left group" });
  await expect(left.locator(".viewer-meta", { hasText: "Live, read-only" })).toBeVisible({ timeout: 20_000 });
  await expect(left.getByTestId("terminal")).toContainText("fake agent ready");
  await expect(page.getByRole("region", { name: "Right group" }).getByTestId("terminal")).toContainText("fake agent ready");

  const brief = join(s.root, "state/plugins/dialogue/dialogues");
  const folder = readdirSync(brief)[0];
  expect(readFileSync(join(brief, folder, "brief.md"), "utf8")).toBe("# Ship the widget\n\nBuild the widget, with tests.\n");

  // Both agents run, but the session list leaves them out. (It may list agents running elsewhere on this machine.)
  const agents = s.tmux("list-sessions", "-F", "#{session_name}").split("\n").filter((name) => /^dia[0-9a-f]{4}[ab]/.test(name));
  expect(agents).toHaveLength(2);
  await workspaces(page).getByRole("button", { name: "Sessions" }).click();
  await expect(page.locator(".session-list")).toBeVisible();
  await expect(page.locator(".session-row", { hasText: /dia[0-9a-f]{4}[ab]/ })).toHaveCount(0);
});

test("a session in a dialogue is labelled in the list", async ({ page }) => {
  const s = await open(page);
  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill("agent-a");
  await dialog.getByRole("button", { name: "Start session" }).click();
  const row = page.locator(".session-row").filter({ hasText: "agent-a" });
  await expect(row).toBeVisible();
  await expect(row).not.toContainText("Dialogue");

  const session = s.tmux("list-sessions", "-F", "#{session_name}").split("\n").find((name) => name.startsWith("agent-a"))!;
  seedDialogue(s, "debate_topic_abcde123", session);
  await page.reload();
  await expect(row).toContainText("Dialogue");
});

test("turning Dialogues off while one runs asks first, then stops it", async ({ page }) => {
  const s = await open(page);
  seedDialogue(s, "debate_topic_running1", "diaabcdea-1", "running");
  const meta = join(s.root, "state/plugins/dialogue/dialogues/debate_topic_running1/meta.json");
  const views = workspaces(page);
  await expect(views.getByRole("button", { name: "Dialogues" })).toBeVisible();

  const status = await openStatus(page);
  const entry = status.getByRole("region", { name: "Plugins" }).getByRole("listitem").filter({ hasText: "Dialogues" });
  await entry.getByRole("switch").click();
  await expect(entry.getByRole("alert")).toContainText("A dialogue is running");
  await entry.getByRole("button", { name: "Keep it on" }).click();
  await expect(entry.getByRole("switch")).toBeChecked();

  await entry.getByRole("switch").click();
  await entry.getByRole("button", { name: "Turn it off" }).click();
  await expect(entry.getByRole("switch")).not.toBeChecked();
  await expect(views.getByRole("button", { name: "Dialogues" })).toHaveCount(0);
  await expect.poll(() => JSON.parse(readFileSync(meta, "utf8")).status).toBe("stopped");
});

test("settings change the theme and terminal font, and persist", async ({ page }) => {
  await open(page);
  const background = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--surface").trim());

  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByLabel("Theme").selectOption("light");
  const light = await background();
  await page.getByLabel("Theme").selectOption("dark");
  expect(await background()).not.toBe(light);
  await page.getByLabel("Terminal font size").fill("16");
  await page.getByRole("button", { name: "Close" }).click();

  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("dark");
  expect(await page.evaluate(() => localStorage.getItem("agentop.fontSize"))).toBe("16");
});

test("Status lists the service's plugins, and switches them", async ({ page }) => {
  await open(page);
  const plugin = (id: string, fields: Record<string, unknown>) => ({
    id,
    name: id,
    version: "0.1.0",
    description: `The ${id} plugin`,
    state: "enabled",
    error: null,
    ui: [],
    ...fields,
  });
  let plugins = [
    plugin("attention", { name: "Waiting for input", state: "disabled" }),
    plugin("broken", { state: "failed", error: "RuntimeError: boom" }),
    plugin("future", { state: "incompatible", error: "Written for plugin API 2. Update this service." }),
    plugin("screens", { ui: ["workspace"] }),
  ];
  const patches: unknown[] = [];
  await page.route("**/api/v2/plugins", (route) => route.fulfill({ json: plugins }));
  await page.route("**/api/v2/plugins/*", (route) => {
    const id = route.request().url().split("/").pop()!;
    const { enabled } = route.request().postDataJSON();
    patches.push({ id, enabled });
    plugins = plugins.map((p) => (p.id === id ? { ...p, state: enabled ? "enabled" : "disabled", error: null } : p));
    return route.fulfill({ json: plugins.find((p) => p.id === id) });
  });
  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();

  // Settings is the app's own; the service's plugins are in its Status.
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("dialog")).not.toContainText("Plugins");
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  const section = (await openStatus(page)).getByRole("region", { name: "Plugins" });
  await expect(section.getByRole("heading")).toHaveText("Plugins on box");

  const attention = section.getByRole("listitem").filter({ hasText: "Waiting for input" });
  await expect(attention).toContainText("The attention plugin");
  // The switch follows the service: it changes once the service confirms.
  await attention.getByRole("switch").click();
  await expect(attention.getByRole("switch")).toBeChecked();
  await expect(attention).toContainText("On");
  expect(patches).toEqual([{ id: "attention", enabled: true }]);

  const broken = section.getByRole("listitem").filter({ hasText: "broken" });
  await expect(broken).toContainText("RuntimeError: boom");
  await expect(broken).toContainText("Restart the service to try loading it again.");
  await expect(broken.getByRole("switch")).toBeDisabled();
  await broken.getByRole("button", { name: "Turn it off" }).click();
  await expect(broken.getByRole("switch")).toBeEnabled();

  const future = section.getByRole("listitem").filter({ hasText: "future" });
  await expect(future).toContainText("Incompatible");
  await expect(future.getByRole("switch")).toBeDisabled();

  await expect(section.getByRole("listitem").filter({ hasText: "screens" })).toContainText("Not available in this app");
});

test("a service with no plugins says so in its Status", async ({ page }) => {
  await page.route("**/api/v2/plugins", (route) => route.fulfill({ json: [] }));
  await open(page);
  await openStatus(page);
  await expect(page.getByText("This service has no plugins.")).toBeVisible();
});
