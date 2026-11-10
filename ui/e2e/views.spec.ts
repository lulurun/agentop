import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
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
  const directory = join(s.root, ".agent-dashboard/dialogues", name);
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
}

test("history can be filtered, resumed, and deleted", async ({ page }) => {
  const s = await open(page);
  let saved = [
    { session_id: "aaa", tool: "claude", title: "Fix login", cwd: join(s.root, "project"), last_active: 1790000000 },
    { session_id: "bbb", tool: "codex", title: "Write docs", cwd: join(s.root, "project"), last_active: 1790000100 },
  ];
  await page.route("**/api/v2/saved-sessions?*", (route) => route.fulfill({ json: saved }));
  await page.route("**/api/v2/saved-sessions/*/*", (route) => {
    saved = saved.filter((item) => !route.request().url().endsWith(item.session_id));
    return route.fulfill({ json: { ok: true } });
  });

  await page.getByRole("button", { name: "History" }).click();
  const view = page.getByRole("region", { name: "History" });
  await expect(view).toContainText("Fix login");
  await view.getByLabel("Filter history").fill("docs");
  await expect(view).not.toContainText("Fix login");
  await view.getByLabel("Filter history").fill("");

  const row = view.getByRole("listitem").filter({ hasText: "Write docs" });
  await row.getByRole("button", { name: "Delete" }).click();
  await row.getByRole("button", { name: "Keep" }).click();
  await expect(view).toContainText("Write docs");
  await row.getByRole("button", { name: "Delete" }).click();
  await row.getByRole("button", { name: "Delete for good" }).click();
  await expect(view).not.toContainText("Write docs");

  await view.getByRole("listitem").filter({ hasText: "Fix login" }).getByRole("button", { name: "Resume" }).click();
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
  await expect(page.getByRole("region", { name: "History" })).toHaveCount(0);
});

test("an empty history says so", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "History" }).click();
  await expect(page.getByText("No past sessions on this service.")).toBeVisible();
});

test("dialogues can be read, and removed after confirming", async ({ page }) => {
  const s = await open(page);
  seedDialogue(s, "debate_topic_abcde123");

  await page.getByRole("button", { name: "Dialogues" }).click();
  await page.getByRole("button", { name: "debate_topic_abcde123" }).click();
  await expect(page.getByText("Decide the plan.")).toBeVisible();
  await expect(page.getByText("They agreed.")).toBeVisible();

  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await page.getByRole("button", { name: "Remove for good" }).click();
  await expect(page.getByText("No dialogues on this service.")).toBeVisible();
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

test("settings list the service's plugins, and switch them", async ({ page }) => {
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
    plugin("screens", { ui: ["left-view"] }),
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

  await page.getByRole("button", { name: "Settings" }).click();
  const section = page.getByRole("region", { name: "Plugins" });
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

test("a service with no plugins says so in settings", async ({ page }) => {
  await page.route("**/api/v2/plugins", (route) => route.fulfill({ json: [] }));
  await open(page);
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.getByText("This service has no plugins.")).toBeVisible();
});
