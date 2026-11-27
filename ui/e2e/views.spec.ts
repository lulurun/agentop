import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";
import { startFromPrompt } from "./start";

const cleanup: (() => unknown)[] = [];
test.afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((f) => f()));
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("agentop.renderer")) localStorage.setItem("agentop.renderer", "dom");
    // These type in the terminal, as a device that chose it over the Input bar does.
    localStorage.setItem("agentop.input.mode", "terminal");
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
  // Each dialogue is a project of the plugin's.
  const directory = join(s.root, "state/projects", name);
  mkdirSync(join(directory, ".project"), { recursive: true });
  writeFileSync(join(directory, ".project", "project.json"), JSON.stringify({ owner: "dialogue", created_at: 0 }));
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

const features = (page: Page) => page.getByRole("navigation", { name: "Features" });

/** Past sessions agentop started, each with a transcript last written `ago` seconds back. */
function seedHistory(
  s: Service,
  past: { id: string; name: string; tool: "claude" | "codex"; agent: string; ago: number; description?: string; transcript?: object[] }[],
) {
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
    writeFileSync(file, p.transcript ? p.transcript.map((r) => JSON.stringify(r) + "\n").join("") : "{}\n");
    utimesSync(file, now - p.ago, now - p.ago);
  }
  mkdirSync(join(s.root, "state/state"), { recursive: true });
  writeFileSync(join(s.root, "state/state/sessions.json"), JSON.stringify({ sessions }));
}

const pastRows = (page: Page) => page.getByRole("navigation", { name: "Past sessions" }).locator(".session-name");

test("history is under the active sessions: filtered, opened, deleted, and resumed under its name", async ({ page }) => {
  const s = await new Service("box").start();
  cleanup.push(() => s.dispose());
  seedHistory(s, [
    {
      id: "fix-login-1",
      name: "fix-login",
      tool: "claude",
      agent: "aaa",
      ago: 7200,
      description: "Login bug",
      transcript: [
        { type: "user", uuid: "u1", timestamp: "2026-11-15T10:00:00Z", message: { role: "user", content: "why does login fail?" } },
        { type: "assistant", uuid: "a1", timestamp: "2026-11-15T10:00:09Z", message: { role: "assistant", content: [{ type: "text", text: "The token expired." }] } },
      ],
    },
    { id: "docs-2", name: "docs", tool: "codex", agent: "bbb", ago: 3600 },
    { id: "scratch-3", name: "scratch", tool: "claude", agent: "ccc", ago: 10_800 },
    // Last used longer ago than the 30-day window.
    { id: "old-4", name: "old", tool: "claude", agent: "ddd", ago: 40 * 86400 },
  ]);
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();

  // History is no feature of its own.
  await expect(features(page).getByRole("button", { name: "History" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Active sessions" })).toContainText("0/20");
  await expect(pastRows(page)).toHaveText(["docs", "fix-login", "scratch"]);
  const history = page.getByRole("region", { name: "History" });
  await history.getByLabel("CLI").selectOption("codex");
  await expect(pastRows(page)).toHaveText(["docs"]);
  await history.getByLabel("CLI").selectOption("");
  await history.getByLabel("Filter history").fill("login bug");
  await expect(pastRows(page)).toHaveText(["fix-login"]);
  await history.getByLabel("Filter history").fill("");

  // Each section folds by its title, and stays folded after a reload.
  await history.getByRole("button", { name: /^History/ }).click();
  await expect(pastRows(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("region", { name: "History" }).getByRole("button", { name: /^History/ })).toHaveAttribute("aria-expanded", "false");
  await history.getByRole("button", { name: /^History/ }).click();
  await expect(pastRows(page)).toHaveText(["docs", "fix-login", "scratch"]);

  // A past session opens in the main area as its conversation; the right panel has what it was.
  const view = page.getByRole("region", { name: "Past session" });
  const right = page.getByRole("complementary", { name: "Right panel" });
  await history.getByRole("button", { name: /fix-login/ }).click();
  const chat = view.getByRole("listitem");
  await expect(chat).toHaveCount(2);
  await expect(chat.nth(0)).toHaveAttribute("data-role", "user");
  await expect(chat.nth(0)).toContainText("why does login fail?");
  await expect(chat.nth(1)).toHaveAttribute("data-role", "assistant");
  await expect(chat.nth(1)).toContainText("The token expired.");
  await expect(right).toContainText("Prompts1");
  await expect(right.getByRole("region", { name: "Files" })).toBeVisible();
  // Another takes its tab's place.
  await history.getByRole("button", { name: /docs/ }).click();
  await expect(right).toContainText("bbb");
  // What its transcript holds: here an empty one, with no tokens recorded.
  await expect(view).toContainText("No messages yet");
  await expect(right).toContainText("Prompts0");
  await expect(right).toContainText("3 B in 1 file");
  await expect(right).toContainText("Not recorded by codex.");
  await history.getByRole("button", { name: /scratch/ }).click();
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["New", "scratch"]);
  await view.getByRole("button", { name: "Delete" }).click();
  await view.getByRole("button", { name: "Keep" }).click();
  await view.getByRole("button", { name: "Delete" }).click();
  await view.getByRole("button", { name: "Delete for good" }).click();
  await expect(pastRows(page)).toHaveText(["docs", "fix-login"]);
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["New"]);
  expect(existsSync(join(s.root, ".claude/projects/-project/ccc.jsonl"))).toBe(false);

  await history.getByRole("button", { name: /fix-login/ }).click();
  await view.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByTestId("terminal").locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
  // Under its own name, and no longer in History.
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["New", "fix-login"]);
  await expect(page.getByRole("navigation", { name: "Sessions", exact: true }).getByText(/^fix-login$/)).toBeVisible();
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

  await startFromPrompt(page, { cwd: join(s.root, "project"), name: "busy" });
  await expect(page.getByRole("region", { name: "Active sessions" })).toContainText("1/1");

  await page.getByRole("region", { name: "History" }).getByRole("button", { name: /fix-login/ }).click();
  await expect(view.getByRole("button", { name: "Resume" })).toBeDisabled();
  await expect(view).toContainText("Active sessions are at the limit. Close a session to resume this one.");
  await page.getByRole("main").getByRole("tab", { name: "New", exact: true }).click();
  await expect(page.locator(".start-note")).toContainText("starting one closes the Sessions session idle longest");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("an empty history says so", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("region", { name: "History" })).toContainText("No sessions ended in the last 30 days.");
});

test("each feature keeps its tabs, and Sessions keeps its terminal connected", async ({ page }) => {
  const s = await open(page);
  await startFromPrompt(page, { cwd: join(s.root, "project"), name: "keeper" });
  const terminal = page.getByTestId("terminal");
  await expect(terminal.locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });

  await page.keyboard.press("Control+Shift+Digit4");
  await expect(features(page).getByRole("button", { name: "Dialogues" })).toHaveAttribute("aria-current", "page");
  // Status opens in this feature, on the left.
  await openStatus(page);
  await expect(page.getByRole("tab", { name: "Status" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["Status"]);

  await page.keyboard.press("Control+Shift+Digit3");
  await expect(page.getByRole("main").getByRole("tab")).toHaveText(["New", "keeper"]);
  await terminal.click();
  await page.keyboard.type("still connected\n");
  await expect(terminal.locator(".xterm-rows")).toContainText("got: still connected", { timeout: 15_000 });

  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await features(page).getByRole("button", { name: "Dialogues" }).click();
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

  await features(page).getByRole("button", { name: "Dialogues" }).click();
  // No tabs to start with: a background, as in Sessions; New dialogue is in the left panel.
  await expect(page.locator(".main .placard")).toContainText("Create a new dialogue, or pick one from the list");
  await expect(page.locator('.workbench[data-feature="plugin:dialogue"]')).toHaveCount(0);
  await expect(page.locator(".header").getByRole("button", { name: "New dialogue", exact: true })).toHaveCount(0);
  await expect(page.locator(".sidebar").getByRole("button", { name: "New dialogue", exact: true })).toHaveCount(1);
  const list = page.locator(".sidebar");
  await list.getByRole("button", { name: /Decide the plan/ }).click();

  const left = page.getByRole("region", { name: "Left area" });
  const right = page.getByRole("region", { name: "Right area" });
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
  await expect(page.getByRole("region", { name: "Right area" })).toHaveCount(0);
});

test("the New dialogue wizard asks for every field, then starts two agents watched read-only and kept out of the session list", async ({ page }) => {
  const s = await open(page);
  await features(page).getByRole("button", { name: "Dialogues" }).click();
  await page.locator(".sidebar").getByRole("button", { name: "New dialogue", exact: true }).click();

  const wizard = page.getByRole("region", { name: "New dialogue", exact: true });
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
  const left = page.getByRole("region", { name: "Left area" });
  await expect(left.locator(".viewer-meta", { hasText: "Live, read-only" })).toBeVisible({ timeout: 20_000 });
  await expect(left.getByTestId("terminal")).toContainText("fake agent ready");
  await expect(page.getByRole("region", { name: "Right area" }).getByTestId("terminal")).toContainText("fake agent ready");

  const brief = join(s.root, "state/projects");
  const folder = readdirSync(brief).find((name) => name !== "default")!;
  expect(readFileSync(join(brief, folder, "brief.md"), "utf8")).toBe("# Ship the widget\n\nBuild the widget, with tests.\n");

  // Both agents run, but the session list leaves them out. (It may list agents running elsewhere on this machine.)
  const agents = s.tmux("list-sessions", "-F", "#{session_name}").split("\n").filter((name) => /^dia[0-9a-f]{4}[ab]/.test(name));
  expect(agents).toHaveLength(2);
  await features(page).getByRole("button", { name: "Sessions" }).click();
  await expect(page.getByRole("navigation", { name: "Sessions", exact: true })).toBeVisible();
  await expect(page.locator(".session-row", { hasText: /dia[0-9a-f]{4}[ab]/ })).toHaveCount(0);
});

test("a session in a dialogue is labelled in the list", async ({ page }) => {
  const s = await open(page);
  await startFromPrompt(page, { cwd: join(s.root, "project"), name: "agent-a" });
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
  const meta = join(s.root, "state/projects/debate_topic_running1/meta.json");
  const views = features(page);
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
    plugin("screens", { ui: ["feature"] }),
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

test("Memory lists recent sessions, searches them like the web, and opens a session or a mention beside the search", async ({ page }) => {
  const s = new Service("box");
  cleanup.push(() => s.dispose());
  const write = (sessionId: string, cwd: string, day: number, texts: [string, string, number][]) => {
    const folder = join(s.root, `.claude/projects/${cwd.replaceAll("/", "-")}`);
    mkdirSync(folder, { recursive: true });
    const records = texts.map(([uuid, text, minute], i) => {
      const type = i % 2 ? "assistant" : "user";
      return {
        type,
        uuid,
        sessionId,
        cwd,
        timestamp: `2026-10-0${day}T12:${String(minute).padStart(2, "0")}:00Z`,
        message: { role: type, content: type === "user" ? text : [{ type: "text", text }] },
      };
    });
    writeFileSync(join(folder, `${sessionId}.jsonl`), records.map((r) => JSON.stringify(r)).join("\n") + "\n");
  };
  write("lunch-1", "/work/lunch", 1, [
    ["u1", "Where should we eat on Friday?", 0],
    ["a1", "Yamada recommended Sushi Dai, see https://example.com/sushidai", 1],
    ...Array.from({ length: 30 }, (_, i): [string, string, number] => [`f${i}`, `Filler message ${i}`, 2 + i]),
  ]);
  write("build-2", "/work/ci", 2, [
    ["u2", "Fix the CI build", 0],
    ["a2", "Fixed the build", 1],
  ]);
  await s.start();
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();

  // No left panel: the search is the left area's pinned tab, over the sessions newest first.
  await features(page).getByRole("button", { name: "Memory" }).click();
  await expect(page.getByRole("button", { name: "Toggle left panel" })).toHaveCount(0);
  const memory = page.locator('.workbench[data-feature="plugin:memory"]');
  const left = memory.locator('.tab-group[data-area="left"]');
  const right = memory.locator('.tab-group[data-area="right"]');
  await expect(left.getByRole("tab", { name: "Search" })).toBeVisible();
  const recent = left.getByRole("list", { name: "Recent sessions" });
  await expect(recent.getByRole("button")).toHaveCount(2);
  await expect(recent.getByRole("button").first()).toContainText("Fix the CI build");
  await expect(recent.getByRole("button").nth(1)).toContainText("Where should we eat on Friday?");

  // A session opens in the right area, and the right panel shows it.
  await recent.getByRole("button").nth(1).click();
  await expect(right.locator(".memory-conversation .message")).toHaveCount(32);
  await expect(page.locator(".details")).toContainText("lunch-1");
  await expect(page.locator(".details")).toContainText("Not summarized yet");

  // Results by scope: a session found by its mention, and the mention itself.
  const box = left.getByRole("searchbox", { name: "Search past conversations" });
  await box.fill("sushi");
  await box.press("Enter");
  const scopes = left.getByRole("tablist", { name: "Results" });
  await expect(scopes.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
  await expect(scopes.getByRole("tab", { name: "Sessions" })).toContainText("1");
  await scopes.getByRole("tab", { name: "Sessions" }).click();
  await expect(left.getByRole("list", { name: "Sessions" })).toContainText("1 mention");
  await scopes.getByRole("tab", { name: "Mentions" }).click();
  const mentions = left.getByRole("list", { name: "Mentions" });
  await expect(mentions.getByRole("button")).toHaveCount(1);
  await expect(mentions.locator("mark").first()).toHaveText("Sushi");
  await expect(mentions).toContainText("You asked: Where should we eat on Friday?");

  // A mention opens its session beside the search, scrolled to it.
  await mentions.getByRole("button").click();
  const hit = right.locator(".memory-conversation .message[data-hit]");
  await expect(hit).toContainText("Yamada recommended");
  await expect(hit.getByRole("link", { name: "https://example.com/sushidai" })).toBeVisible();
  await expect(hit).toBeInViewport();
  await expect(page.locator(".details")).toContainText("lunch-1");

  // Back on the search, the right panel still shows the conversation beside it.
  await box.click();
  await expect(page.locator(".details")).toContainText("lunch-1");

  await box.fill("ramen");
  await left.locator("form[role=search]").getByRole("button", { name: "Search" }).click();
  await expect(left).toContainText("Nothing has all of: ramen");
  await left.getByRole("button", { name: "Didn't find it?" }).click();
  await left.getByRole("textbox", { name: "What were you looking for?" }).fill("the ramen shop");
  await left.getByRole("button", { name: "Note the miss" }).click();
  await expect(left.getByRole("status")).toContainText("“sushi”, “ramen”");
  const noted = readFileSync(join(s.root, "state/plugins/memory/recall.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line));
  expect(noted.at(-1)).toMatchObject({ type: "miss", kind: "nothing", looked_for: "the ramen shop", tried: ["sushi", "ramen"] });

  // Clearing the box goes back to the recent sessions.
  await box.fill("");
  await expect(recent.getByRole("button")).toHaveCount(2);
});

test("a project is made with a name, opens its Coordinator, and keeps its sessions out of Sessions", async ({ page }) => {
  // Its Coordinator is typed the whole Coordinator skill as its first prompt, which takes a while.
  test.setTimeout(150_000);
  const s = await open(page);
  await features(page).getByRole("button", { name: "Projects" }).click();
  const left = page.getByRole("complementary", { name: "Left panel" });
  await left.getByLabel("New project's name").fill("alpha");
  await left.getByRole("button", { name: "Create", exact: true }).click();

  // Its Coordinator works in the project folder, with its instructions there, and opens in Projects.
  await expect(left.getByRole("navigation", { name: "Projects" }).locator('[aria-current="true"]')).toContainText("alpha", { timeout: 20_000 });
  const sessions = left.getByRole("navigation", { name: "Sessions", exact: true });
  await expect(sessions.getByText("Coordinator")).toBeVisible({ timeout: 45_000 });
  await expect(page.getByTestId("terminal")).toContainText("fake agent ready", { timeout: 45_000 });
  expect(existsSync(join(s.root, "state/projects/alpha/AGENTS.md"))).toBe(true);
  expect(readFileSync(join(s.root, "state/projects/alpha/CLAUDE.md"), "utf8")).toBe("@AGENTS.md\n");

  // Sessions shows the default project's only.
  await features(page).getByRole("button", { name: "Sessions" }).click();
  await expect(page.getByRole("navigation", { name: "Sessions", exact: true }).getByText("Coordinator")).toHaveCount(0);

  // A second one, named straight after, is made and chosen too.
  await features(page).getByRole("button", { name: "Projects" }).click();
  await left.getByLabel("New project's name").fill("beta");
  await left.getByRole("button", { name: "Create", exact: true }).click();
  await expect(left.getByRole("navigation", { name: "Projects" }).locator('[aria-current="true"]')).toContainText("beta", { timeout: 20_000 });

  await expect(left.getByRole("button", { name: "Close beta" })).toHaveCount(0);
  const agents = readFileSync(join(s.root, "state/projects/beta/AGENTS.md"), "utf8");
  const coordinatorPosts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/projects\/[^/]+\/(coordinator|close)$/.test(new URL(request.url()).pathname)) {
      coordinatorPosts.push(request.url());
    }
  });
  await expect(page.locator('[data-testid="terminal"]:visible')).toBeVisible({ timeout: 45_000 });
  const registryPath = join(s.root, "state/projects/beta/.project/sessions.json");
  const records = JSON.parse(readFileSync(registryPath, "utf8")).sessions;
  const betaId = Object.keys(records).find((id) => records[id].display_name === "Coordinator")!;
  s.tmux("kill-session", "-t", betaId);
  await expect(left.getByRole("navigation", { name: "Sessions", exact: true }).getByText("Coordinator")).toHaveCount(0, { timeout: 15_000 });
  await left.getByRole("button", { name: "alpha", exact: true }).click();
  await left.getByRole("button", { name: "beta", exact: true }).click();
  await page.reload();
  await expect(left.getByRole("navigation", { name: "Projects" }).locator('[aria-current="true"]')).toContainText("beta");
  await expect(left.getByRole("navigation", { name: "Sessions", exact: true }).getByText("Coordinator")).toHaveCount(0);
  expect(Object.keys(JSON.parse(readFileSync(registryPath, "utf8")).sessions)).toEqual(Object.keys(records));
  expect(coordinatorPosts).toEqual([]);
  expect(existsSync(join(s.root, "state/projects/beta/.project/project.json"))).toBe(true);

  // When the Coordinator has closed it (its entry gone), it leaves the list by itself.
  writeFileSync(join(s.root, "state/projects/beta/track/1/CLOSED.md"), "# Closed\n\n- 2026-11-25 10:00 closed by `Coordinator` of beta\n");
  rmSync(join(s.root, "state/projects/beta/.project/project.json"));
  const list = left.getByRole("navigation", { name: "Projects" });
  await expect(list.getByRole("button", { name: "beta", exact: true })).toHaveCount(0, { timeout: 15_000 });
  await expect(list.getByRole("button", { name: "alpha", exact: true })).toBeVisible();

  // Made again by name, the kept folder is loaded back, with its track, and its Coordinator starts on it.
  await left.getByLabel("New project's name").fill("beta");
  await left.getByRole("button", { name: "Create", exact: true }).click();
  await expect(list.locator('[aria-current="true"]')).toContainText("beta", { timeout: 20_000 });
  expect(readFileSync(join(s.root, "state/projects/beta/AGENTS.md"), "utf8")).toBe(agents);
  expect(readFileSync(join(s.root, "state/projects/beta/track/1/CLOSED.md"), "utf8")).toMatch(/reopened by the owner, in the app\n$/);
});

test("each project shows its own tabs and panels from its layout.json, and Sessions has one too", async ({ page }) => {
  const s = await open(page);
  const project = (name: string) => {
    const directory = join(s.root, "state/projects", name);
    mkdirSync(join(directory, ".project"), { recursive: true });
    writeFileSync(join(directory, ".project", "project.json"), JSON.stringify({ owner: "projects", created_at: 0 }));
    return join(directory, ".project", "layout.json");
  };
  const alpha = project("alpha");
  const beta = project("beta");
  // Written by hand: a file tab on the left, the right panel shut.
  const byHand = {
    version: 1,
    "left-panel": { open: true },
    "mid-panel": {
      left: { open: true, tabs: [{ id: "x", content: { kind: "file", file: { sessionId: "gone-abc123", source: "workdir", path: "docs/fileX.md" } } }], active: "x" },
      right: { open: false, tabs: [], active: null },
    },
    "right-panel": { open: false },
    focused: "left",
  };
  writeFileSync(alpha, JSON.stringify(byHand));

  await features(page).getByRole("button", { name: "Projects" }).click();
  const left = page.getByRole("complementary", { name: "Left panel" });
  const tabsShown = page.getByRole("main").locator('.workbench:not([hidden]) [role="tab"]');
  const rightPanel = page.getByRole("complementary", { name: "Right panel" });
  await left.getByRole("button", { name: "alpha", exact: true }).click();
  await expect(tabsShown).toHaveText(["fileX.md"]);
  await expect(rightPanel).toHaveCount(0);

  // fileX is alpha's: beta starts with no tabs, and its own panels.
  await left.getByRole("button", { name: "beta", exact: true }).click();
  await expect(tabsShown).toHaveCount(0);
  await expect(rightPanel).toHaveCount(1);
  await openStatus(page);
  await expect(tabsShown).toHaveText(["Status"]);
  await expect.poll(() => existsSync(beta) && JSON.parse(readFileSync(beta, "utf8"))["mid-panel"].left.tabs[0]?.content.kind).toBe("status");

  await left.getByRole("button", { name: "alpha", exact: true }).click();
  await expect(tabsShown).toHaveText(["fileX.md"]);
  await expect(rightPanel).toHaveCount(0);
  expect(JSON.parse(readFileSync(alpha, "utf8"))).toEqual(byHand);

  await page.reload();
  await expect(page.locator(".link-connected")).toBeVisible();
  await expect(tabsShown).toHaveText(["fileX.md"]);
  await left.getByRole("button", { name: "beta", exact: true }).click();
  await expect(tabsShown).toHaveText(["Status"]);

  // Sessions is the default project, with its file in the default project's folder.
  await features(page).getByRole("button", { name: "Sessions" }).click();
  await expect(tabsShown).toHaveText(["New"]);
  await page.keyboard.press("Control+Shift+Digit0");
  const sessions = join(s.root, "state/projects/default/.project/layout.json");
  await expect.poll(() => existsSync(sessions) && JSON.parse(readFileSync(sessions, "utf8"))["right-panel"].open).toBe(false);
  await features(page).getByRole("button", { name: "Projects" }).click();
  await expect(tabsShown).toHaveText(["Status"]);
  await expect(rightPanel).toHaveCount(1);
});
