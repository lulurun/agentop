import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";
import { startFromPrompt } from "./start";

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
  await page.addInitScript(() => {
    localStorage.setItem("agentop.renderer", "dom");
    // These type in the terminal, as a device that chose it over the Input bar does.
    localStorage.setItem("agentop.input.mode", "terminal");
  });
});

async function startSession(page: Page, name: string, cwd: string) {
  await startFromPrompt(page, { cwd: cwd, name: name, tool: "claude" });
  await expect(page.getByRole("tab", { name, selected: true })).toBeVisible();
  await expect(shownTerminals(page).first().locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
}

/** The terminals on screen, not those kept out of sight for a quick return. */
const shownTerminals = (page: Page) => page.locator(".tab-pane:not([hidden])").getByTestId("terminal");

const row = (page: Page, name: string) => page.getByRole("navigation", { name: "Sessions" }).getByRole("button", { name });

test("clicking a session switches the tab, and a new tab or the side keeps both", async ({ page }) => {
  const s = await service("alpha");
  const other = join(s.root, "other");
  mkdirSync(other);
  await page.goto(s.signedInUrl);
  await startSession(page, "one", join(s.root, "project"));
  await startSession(page, "two", other);

  const main = page.locator(".main");
  // Each start opens a tab beside the start buffer, the newest first.
  await expect(main.getByRole("tab")).toHaveText(["New", "two", "one"]);

  // Clicking a session already in a tab shows that tab.
  await row(page, "one").click();
  await expect(main.getByRole("tab")).toHaveText(["New", "two", "one"]);
  await expect(main.getByRole("tab", { name: "one" })).toHaveAttribute("aria-selected", "true");
  // Ctrl+Shift+A to K pick the active area's tabs by position.
  await page.keyboard.press("Control+Shift+KeyS");
  await expect(main.getByRole("tab", { name: "two" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Control+Shift+KeyD");
  await expect(main.getByRole("tab", { name: "one" })).toHaveAttribute("aria-selected", "true");

  await row(page, "two").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Open in the right area" }).click();
  await expect(page.getByRole("region", { name: "Right area" }).getByRole("tab")).toHaveText(["two"]);
  await expect(shownTerminals(page)).toHaveCount(2);
  // Ctrl+Shift+W and E make the left and right area active.
  await page.keyboard.press("Control+Shift+KeyW");
  await expect(page.getByRole("region", { name: "Left area" })).toHaveAttribute("data-focused", "true");
  await page.keyboard.press("Control+Shift+KeyE");
  await expect(page.getByRole("region", { name: "Right area" })).toHaveAttribute("data-focused", "true");

  // The right panel follows the focused group.
  await page.getByRole("region", { name: "Left area" }).getByRole("tab", { name: "one" }).click();
  await expect(page.locator(".details").getByRole("button", { name: "one", exact: true })).toBeVisible();

  await page.getByRole("region", { name: "Right area" }).getByRole("button", { name: "Close two" }).click();
  await expect(page.getByRole("region", { name: "Right area" })).toHaveCount(0);
  await expect(main.getByRole("tab")).toHaveText(["New", "two", "one"]);

  await page.reload();
  await expect(page.locator(".main").getByRole("tab")).toHaveText(["New", "two", "one"]);
  // Ctrl+Shift+L closes the active tab; the session keeps running. The start buffer can't be closed.
  await page.keyboard.press("Control+Shift+KeyL");
  await expect(page.locator(".main").getByRole("tab")).toHaveText(["New", "two"]);
  await page.keyboard.press("Control+Shift+KeyA");
  await page.keyboard.press("Control+Shift+KeyL");
  await expect(page.locator(".main").getByRole("tab")).toHaveText(["New", "two"]);
  await expect(row(page, "one")).toBeVisible();
});

test("a session shown before keeps its terminal, and only the last few are kept", async ({ page }) => {
  const s = await service("alpha");
  await page.goto(s.signedInUrl);
  const names = ["one", "two", "three", "four"];
  for (const name of names) {
    const cwd = join(s.root, name);
    mkdirSync(cwd);
    await startSession(page, name, cwd);
  }
  // Each start opened a tab; the last few shown stay on the page out of sight.
  await expect(page.locator(".main").getByRole("tab")).toHaveText(["New", "four", "three", "two", "one"]);
  await expect(page.getByTestId("terminal")).toHaveCount(3);
  await expect(shownTerminals(page)).toHaveCount(1);

  // Going back to a kept session shows the same terminal, with no new one built.
  await page.evaluate(() => {
    const kept = [...document.querySelectorAll(".tab-pane[hidden] [data-testid=terminal]")];
    kept.forEach((terminal) => terminal.setAttribute("data-mark", "kept"));
  });
  await row(page, "three").click();
  await expect(page.getByRole("tab", { name: "three", selected: true })).toBeVisible();
  await expect(shownTerminals(page)).toHaveAttribute("data-mark", "kept");
  await expect(shownTerminals(page).locator(".xterm-rows")).toContainText("fake agent ready");
  // It takes the keyboard back, with no click.
  await page.keyboard.type("back again\n");
  await expect(shownTerminals(page).locator(".xterm-rows")).toContainText("got: back again");

  // "one" was the fourth, so it was dropped, and comes back as a new terminal.
  await row(page, "one").click();
  await expect(page.getByRole("tab", { name: "one", selected: true })).toBeVisible();
  await expect(shownTerminals(page)).not.toHaveAttribute("data-mark", "kept");
  await expect(shownTerminals(page).locator(".xterm-rows")).toContainText("fake agent ready", { timeout: 15_000 });
  await expect(page.getByTestId("terminal")).toHaveCount(3);

  // Closing a kept session's tab lets it go.
  await page.getByRole("button", { name: "Close one" }).click();
  await expect(page.getByTestId("terminal")).toHaveCount(2);
});

test("a file from the right panel opens in the right group, beside the terminal", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  mkdirSync(join(project, "src"), { recursive: true });
  writeFileSync(join(project, "src", "hello.py"), "print('hello')\nprint('again')\n");
  writeFileSync(join(project, "notes.txt"), "notes\n");
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  const details = page.locator(".details");
  await details.getByRole("button", { name: "All" }).click();
  await details.getByRole("button", { name: "src" }).click();
  await details.getByRole("button", { name: "hello.py" }).click();

  const left = page.getByRole("region", { name: "Left area" });
  const right = page.getByRole("region", { name: "Right area" });
  await expect(left.getByRole("tab")).toHaveText(["New", "demo"]);
  await expect(right.getByRole("tab")).toHaveText(["hello.py"]);
  await expect(right).toHaveAttribute("data-focused", "true");
  await expect(right.getByLabel("Contents of src/hello.py")).toHaveText("print('hello')\nprint('again')\n");
  await expect(right.getByText("2 lines")).toBeVisible();
  // Coloured as Python, with the text unchanged.
  await expect(right.getByLabel("Contents of src/hello.py").locator(".hljs-string").first()).toHaveText("'hello'");
  await expect(page.getByTestId("terminal")).toBeVisible();

  // Clicking the session goes back to the left; another file still opens on the right.
  await row(page, "demo").click();
  await expect(left).toHaveAttribute("data-focused", "true");
  await details.getByRole("button", { name: "notes.txt" }).click();
  await expect(right.getByRole("tab")).toHaveText(["hello.py", "notes.txt"]);
  await expect(right).toHaveAttribute("data-focused", "true");

  // A file can still be moved to the left.
  await right.getByRole("tab", { name: "hello.py" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the left area" }).click();
  await expect(left.getByRole("tab")).toHaveText(["New", "demo", "hello.py"]);
  await expect(right.getByRole("tab")).toHaveText(["notes.txt"]);
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
      "```python",
      "def hello():",
      "    return 1",
      "```",
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
  await details.getByRole("button", { name: "All" }).click();
  await details.getByRole("button", { name: "docs" }).click();
  await details.getByRole("button", { name: "guide.md" }).click();

  const main = page.locator(".main");
  const preview = main.getByLabel("Preview of docs/guide.md");
  await expect(preview.getByRole("heading", { level: 1, name: "Guide" })).toBeVisible();
  await expect(preview.locator("strong")).toHaveText("this");
  await expect(preview.getByRole("cell", { name: "claude" })).toBeVisible();
  await expect(preview.getByRole("checkbox")).toBeChecked();
  await expect(preview.locator("pre code .hljs-keyword").first()).toHaveText("def");
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

  // The app drawing itself again leaves the page's elements alone, so a selection being made survives.
  await preview.getByRole("link", { name: "the other page" }).evaluate((a) => ((a as HTMLElement).dataset.kept = "yes"));
  await page.getByRole("button", { name: "Toggle right panel" }).click();
  await page.getByRole("button", { name: "Toggle right panel" }).click();
  await expect(details).toBeVisible();
  await expect(preview.getByRole("link", { name: "the other page" })).toHaveAttribute("data-kept", "yes");

  // A link to a file beside it opens that file in a tab.
  await preview.getByRole("link", { name: "the other page" }).click();
  await expect(main.getByRole("tab")).toHaveText(["New", "demo", "guide.md", "other.md"]);
  await expect(main.getByLabel("Preview of docs/other.md").getByRole("heading", { name: "Other page" })).toBeVisible();
});

test("an image file opens drawn in the middle of the tab", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  // A 1x1 PNG.
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==", "base64");
  writeFileSync(join(project, "dot.PNG"), png);
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  const details = page.locator(".details");
  await details.getByRole("button", { name: "All" }).click();
  await details.getByRole("button", { name: "dot.PNG" }).click();

  const main = page.locator(".main");
  await expect(main.getByRole("tab")).toHaveText(["New", "demo", "dot.PNG"]);
  const image = main.getByRole("img", { name: "dot.PNG" });
  await expect(image).toHaveAttribute("src", /^data:image\/png;base64,/);
  expect(await image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBe(1);
  // In the middle of the area below the bar.
  const [box, area] = [await image.boundingBox(), await main.locator(".viewer-image").boundingBox()];
  expect(Math.abs(box!.x + box!.width / 2 - (area!.x + area!.width / 2))).toBeLessThan(2);
  expect(Math.abs(box!.y + box!.height / 2 - (area!.y + area!.height / 2))).toBeLessThan(2);
  await expect(main.getByText(`${png.length} B`)).toBeVisible();
  await expect(main.getByRole("button", { name: "Wrap" })).toHaveCount(0);
});

test("a file in the Changed list opens as its diff, and Open file shows its text", async ({ page }) => {
  const s = await service("alpha");
  const project = join(s.root, "project");
  mkdirSync(project, { recursive: true });
  const git = (...args: string[]) =>
    execFileSync("git", ["-C", project, "-c", "user.name=t", "-c", "user.email=t@example.com", ...args]);
  writeFileSync(join(project, "app.py"), "one\ntwo\nthree\n");
  writeFileSync(join(project, "gone.txt"), "bye\n");
  git("init", "-q");
  git("add", ".");
  git("commit", "-q", "-m", "first");
  writeFileSync(join(project, "app.py"), "one\ndeux\nthree\n");
  rmSync(join(project, "gone.txt"));
  await page.goto(s.signedInUrl);
  await startSession(page, "demo", project);

  const details = page.locator(".details");
  await details.getByRole("button", { name: "app.py" }).click();
  const main = page.locator(".main");
  await expect(main.getByRole("tab")).toHaveText(["New", "demo", "app.py (diff)"]);
  const diff = main.getByLabel("Changes to app.py");
  await expect(diff.locator('.diff-row[data-kind="del"]')).toHaveText(["2-two"]);
  await expect(diff.locator('.diff-row[data-kind="add"]')).toHaveText(["2+deux"]);
  await expect(main.locator(".viewer-meta")).toHaveText("+1 −1");

  await main.getByRole("button", { name: "Open file" }).click();
  await expect(main.getByRole("tab")).toHaveText(["New", "demo", "app.py (diff)", "app.py"]);
  await expect(main.getByLabel("Contents of app.py")).toHaveText("one\ndeux\nthree\n");

  // A deleted file opens too, as its removed lines, with nothing to open.
  await details.getByRole("button", { name: "gone.txt" }).click();
  await expect(main.getByLabel("Changes to gone.txt").locator('.diff-row[data-kind="del"]')).toHaveText(["1-bye"]);
  await expect(main.getByRole("button", { name: "Open file" })).toHaveCount(0);
});
