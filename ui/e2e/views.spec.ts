import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync, readFileSync, utimesSync, writeFileSync } from "node:fs";
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

/** Past sessions agentop started, each with a transcript last written `ago` seconds back. */
function seedHistory(s: Service, past: { id: string; name: string; tool: "claude" | "codex"; agent: string; ago: number; description?: string }[]) {
  const sessions: Record<string, unknown> = {};
  const now = Date.now() / 1000;
  for (const p of past) {
    sessions[p.id] = {
      managed: true,
      tool: p.tool,
      agent_session_id: p.agent,
      display_name: p.name,
      description: p.description,
      cwd: join(s.root, "project"),
      created_at: now - p.ago - 600,
      ended_at: now - p.ago,
    };
    const folder = p.tool === "claude" ? join(s.root, ".claude/projects/-project") : join(s.root, ".codex/sessions/2026/11/15");
    mkdirSync(folder, { recursive: true });
    const file = join(folder, p.tool === "claude" ? `${p.agent}.jsonl` : `rollout-2026-11-15-${p.agent}.jsonl`);
    writeFileSync(file, "{}\n");
    utimesSync(file, now - p.ago, now - p.ago);
  }
  mkdirSync(join(s.root, "state/state"), { recursive: true });
  writeFileSync(join(s.root, "state/state/sessions.json"), JSON.stringify({ sessions }));
}

const pastRows = (page: Page) => page.getByRole("navigation", { name: "Past sessions" }).locator(".session-name");

test("history is under the active sessions: filtered, opened, deleted, and resumed with its date", async ({ page }) => {
  const s = await new Service("box").start();
  cleanup.push(() => s.dispose());
  seedHistory(s, [
    { id: "fix-login-1", name: "fix-login", tool: "claude", agent: "aaa", ago: 7200, description: "Login bug" },
    { id: "docs-2", name: "docs", tool: "codex", agent: "bbb", ago: 3600 },
    { id: "scratch-3", name: "scratch", tool: "claude", agent: "ccc", ago: 10_800 },
    // Last used longer ago than the 30-day window.
    { id: "old-4", name: "old", tool: "claude", agent: "ddd", ago: 40 * 86400 },
  ]);
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();

  // History is no workspace of its own.
  await expect(workspaces(page).getByRole("button", { name: "History" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Active sessions" })).toContainText("0/20");
  await expect(pastRows(page)).toHaveText(["docs", "fix-login", "scratch"]);
  const history = page.getByRole("region", { name: "History" });
  await history.getByLabel("CLI").selectOption("codex");
  await expect(pastRows(page)).toHaveText(["docs"]);
  await history.getByLabel("CLI").selectOption("");
  await history.getByLabel("Filter history").fill("login bug");
  await expect(pastRows(page)).toHaveText(["fix-login"]);
  await history.getByLabel("Filter history").fill("");

  // A past session opens in the main area; another takes its tab's place.
  const view = page.getByRole("region", { name: "Past session" });
  await history.getByRole("button", { name: /docs/ }).click();
  await expect(view).toContainText("bbb");
  await history.getByRole("button", { name: /scratch/ }).click();
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["scratch"]);
  await view.getByRole("button", { name: "Delete" }).click();
  await view.getByRole("button", { name: "Keep" }).click();
  await view.getByRole("button", { name: "Delete" }).click();
  await view.getByRole("button", { name: "Delete for good" }).click();
  await expect(pastRows(page)).toHaveText(["docs", "fix-login"]);
  await expect(page.getByRole("main").getByRole("tab")).toHaveCount(0);
  expect(existsSync(join(s.root, ".claude/projects/-project/ccc.jsonl"))).toBe(false);

  await history.getByRole("button", { name: /fix-login/ }).click();
  await view.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
  // Named for when it was last used, and no longer in History.
  await expect(page.getByRole("main").getByRole("tab")).toHaveText([/^fix-login \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/]);
  await expect(page.getByRole("navigation", { name: "Sessions", exact: true }).getByText(/^fix-login 20/)).toBeVisible();
  await expect(pastRows(page)).toHaveText(["docs"]);
  await expect(page.getByRole("region", { name: "Active sessions" })).toContainText("1/20");
});

test("resuming waits for a session to close once the active sessions are at the limit", async ({ page }) => {
  const s = await new Service("box").start();
  cleanup.push(() => s.dispose());
  seedHistory(s, [{ id: "fix-login-1", name: "fix-login", tool: "claude", agent: "aaa", ago: 7200 }]);
  await page.route("**/api/v2/history", async (route) => {
    const response = await route.fetch();
    return route.fulfill({ response, json: { ...(await response.json()), max_active: 1 } });
  });
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();

  const view = page.getByRole("region", { name: "Past session" });
  await page.getByRole("region", { name: "History" }).getByRole("button", { name: /fix-login/ }).click();
  await expect(view.getByRole("button", { name: "Resume" })).toBeEnabled();

  await page.getByRole("button", { name: "New session" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill("busy");
  await dialog.getByRole("button", { name: "Start session" }).click();
  await expect(page.getByRole("region", { name: "Active sessions" })).toContainText("1/1");

  await page.getByRole("region", { name: "History" }).getByRole("button", { name: /fix-login/ }).click();
  await expect(view.getByRole("button", { name: "Resume" })).toBeDisabled();
  await expect(view).toContainText("Active sessions are at the limit. Close a session to resume this one.");
  await page.getByRole("button", { name: "New session" }).first().click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Start session" })).toBeDisabled();
});

test("an empty history says so", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("region", { name: "History" })).toContainText("No sessions ended in the last 30 days.");
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
  await expect(workspaces(page).getByRole("button", { name: "Dialogues" })).toHaveAttribute("aria-current", "page");
  // Status opens in this workspace, on the left.
  await openStatus(page);
  await expect(page.getByRole("tab", { name: "Status" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["Status"]);

  await page.keyboard.press("Control+Shift+Digit1");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["keeper"]);
  await terminal.click();
  await page.keyboard.type("still connected\n");
  await expect(terminal.locator(".xterm-rows")).toContainText("got: still connected", { timeout: 15_000 });

  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await workspaces(page).getByRole("button", { name: "Dialogues" }).click();
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["Status"]);
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
  await expect(page.getByRole("navigation", { name: "Sessions", exact: true })).toBeVisible();
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

test("the Memory workspace searches past conversations, opens one at the hit, and notes a miss", async ({ page }) => {
  const s = new Service("box");
  cleanup.push(() => s.dispose());
  const log = join(s.root, ".claude/projects/-work-lunch");
  mkdirSync(log, { recursive: true });
  const say = (uuid: string, type: "user" | "assistant", text: string, minute: number) => ({
    type,
    uuid,
    sessionId: "lunch-1",
    cwd: "/work/lunch",
    timestamp: `2026-10-01T12:${String(minute).padStart(2, "0")}:00Z`,
    message: { role: type, content: type === "user" ? text : [{ type: "text", text }] },
  });
  const records = [
    say("u1", "user", "Where should we eat on Friday?", 0),
    say("a1", "assistant", "Yamada recommended Sushi Dai, see https://example.com/sushidai", 1),
    ...Array.from({ length: 30 }, (_, i) => say(`f${i}`, i % 2 ? "assistant" : "user", `Filler message ${i}`, 2 + i)),
  ];
  writeFileSync(join(log, "lunch-1.jsonl"), records.map((r) => JSON.stringify(r)).join("\n") + "\n");
  await s.start();
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();

  await workspaces(page).getByRole("button", { name: "Memory" }).click();
  await expect(page.locator(".main .placard")).toContainText("Search past conversations");
  const panel = page.locator(".sidebar");
  await panel.getByRole("searchbox", { name: "Search past conversations" }).fill("sushi");
  await panel.getByRole("searchbox", { name: "Search past conversations" }).press("Enter");
  const hits = panel.getByRole("list", { name: "Messages" });
  await expect(hits.getByRole("button")).toHaveCount(1);
  await expect(hits.locator("mark").first()).toHaveText("Sushi");
  await expect(hits).toContainText("You asked: Where should we eat on Friday?");
  await hits.getByRole("button").click();

  const tab = page.getByRole("main").locator(".memory-conversation");
  await expect(tab.locator(".message")).toHaveCount(32);
  const hit = tab.locator(".message[data-hit]");
  await expect(hit).toContainText("Yamada recommended");
  await expect(hit.getByRole("link", { name: "https://example.com/sushidai" })).toBeVisible();
  await expect(hit).toBeInViewport();
  await expect(page.locator(".details")).toContainText("lunch-1");
  await expect(page.locator(".details")).toContainText("32");
  await expect(page.locator(".details")).toContainText("Not summarized yet");

  await panel.getByRole("searchbox", { name: "Search past conversations" }).fill("ramen");
  await panel.getByRole("button", { name: "Search" }).click();
  await expect(panel).toContainText("No message has all of: ramen");
  await panel.getByRole("button", { name: "Didn't find it?" }).click();
  await panel.getByRole("textbox", { name: "What were you looking for?" }).fill("the ramen shop");
  await panel.getByRole("button", { name: "Note the miss" }).click();
  await expect(panel.getByRole("status")).toContainText("“sushi”, “ramen”");
  const noted = readFileSync(join(s.root, "state/plugins/memory/recall.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  expect(noted.at(-1)).toMatchObject({ type: "miss", kind: "nothing", looked_for: "the ramen shop", tried: ["sushi", "ramen"] });
});
