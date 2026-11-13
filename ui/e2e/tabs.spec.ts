import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
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

test("a Markdown file opens drawn, with its links and images", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  mkdirSync(join(project, "docs", "img"), { recursive: true });
  // A 1x1 PNG.
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==", "base64");
  writeFileSync(join(project, "docs", "img", "dot.png"), png);
  writeFileSync(join(project, "docs", "other.md"), "# Other page\n");
  writeFileSync(
    join(project, "docs", "guide.md"),
    [
      "# Guide",
      "",
      "Read **this** and [the other page](other.md), or [jump](#later).",
      "",
      "| Tool | Kind |",
      "| --- | --- |",
      "| claude | agent |",
      "",
      "- [x] done",
      "",
      "![a dot](img/dot.png)",
      "",
      "<script>window.ran = true</script>",
      '<details><summary>More</summary><img src="x" onerror="window.ran = true"> hidden</details>',
      "",
      "## Later",
      "",
      "See https://example.com/docs.",
    ].join("\n"),
  );
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  const details = page.locator(".details");
  await details.getByRole("tab", { name: "All" }).click();
  await details.getByRole("button", { name: "docs" }).click();
  await details.getByRole("button", { name: "guide.md" }).click();

  const main = page.locator(".main");
  const preview = main.getByLabel("Preview of docs/guide.md");
  await expect(preview.getByRole("heading", { level: 1, name: "Guide" })).toBeVisible();
  await expect(preview.locator("strong")).toHaveText("this");
  await expect(preview.getByRole("cell", { name: "claude" })).toBeVisible();
  await expect(preview.getByRole("checkbox")).toBeChecked();
  await expect(preview.getByRole("link", { name: "https://example.com/docs" })).toHaveAttribute("target", "_blank");
  await expect(preview.getByRole("img", { name: "a dot" })).toHaveAttribute("src", /^data:image\/png;base64,/);
  // HTML is drawn, but nothing in it runs.
  await expect(preview.locator("details summary")).toHaveText("More");
  await expect(preview).not.toContainText("window.ran");
  expect(await page.evaluate(() => (window as { ran?: boolean }).ran)).toBeUndefined();

  // Raw shows the text, and Preview comes back.
  await main.getByRole("button", { name: "Raw" }).click();
  await expect(main.getByLabel("Contents of docs/guide.md")).toContainText("# Guide");
  await main.getByRole("button", { name: "Preview" }).click();

  // A link to a file beside it opens that file in a tab.
  await preview.getByRole("link", { name: "the other page" }).click();
  await expect(main.getByRole("tab")).toHaveText(["demo", "guide.md", "other.md"]);
  await expect(main.getByLabel("Preview of docs/other.md").getByRole("heading", { name: "Other page" })).toBeVisible();
});

test("a transcript opens as a conversation, with its links", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  // The fake agent recorded its session ID; put a transcript where Claude Code would.
  const sessions = join(s.root, ".claude", "sessions");
  const sessionId = JSON.parse(readFileSync(join(sessions, readdirSync(sessions)[0]), "utf8")).sessionId;
  const folder = join(s.root, ".claude", "projects", project.replaceAll("/", "-"));
  mkdirSync(folder, { recursive: true });
  const at = "2026-11-01T10:00:00Z";
  const records = [
    { type: "user", uuid: "u1", sessionId, timestamp: at, message: { role: "user", content: "who sent the atama link?" } },
    { type: "assistant", uuid: "a1", sessionId, timestamp: at, message: { content: [{ type: "tool_use", id: "t", name: "Bash", input: {} }] } },
    { type: "assistant", uuid: "a2", sessionId, timestamp: at, message: { content: [{ type: "text", text: "Yamada sent https://example.com/atama." }] } },
  ];
  writeFileSync(join(folder, `${sessionId}.jsonl`), records.map((r) => JSON.stringify(r)).join("\n") + "\n");

  const details = page.locator(".details");
  const entry = details.getByRole("button", { name: `${sessionId}.jsonl` });
  await expect(async () => {
    await page.reload();
    await expect(entry).toBeVisible({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  await entry.click();

  const conversation = page.locator(".main").getByLabel(`Conversation in ${sessionId}.jsonl`);
  await expect(conversation.locator(".message")).toHaveCount(2);
  await expect(conversation.locator(".message").first()).toContainText("who sent the atama link?");
  await expect(conversation.getByRole("link", { name: "https://example.com/atama" })).toHaveAttribute("href", "https://example.com/atama");
  await expect(page.locator(".main").getByText("2 messages")).toBeVisible();

  await page.locator(".main").getByRole("button", { name: "Raw" }).click();
  await expect(page.locator(".main").getByLabel(`Contents of ${sessionId}.jsonl`)).toContainText('"tool_use"');
});
