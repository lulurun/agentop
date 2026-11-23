import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

let s: Service;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("agentop.renderer", "dom");
  });
});

test.afterEach(async () => {
  await s.dispose();
});

/** A service with the Input bar on, as it is by default. */
async function withInputBar(page: Page) {
  s = new Service("alpha");
  mkdirSync(join(s.root, "state", "state"), { recursive: true });
  writeFileSync(join(s.root, "state", "state", "plugins.json"), JSON.stringify({ input: { enabled: true } }));
  await s.start();
  await page.goto(s.signedInUrl);
  await page.getByRole("button", { name: "New session", exact: true }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Working directory").fill(join(s.root, "project"));
  await dialog.getByLabel("Name").fill("phone");
  await dialog.getByRole("button", { name: "Start session" }).click();
  await expect(screen(page)).toContainText("fake agent ready", { timeout: 15_000 });
}

const screen = (page: Page) => page.getByTestId("terminal").locator(".xterm-rows");

test("prompts and keys go through the bar, and the terminal is only watched", async ({ page }) => {
  await withInputBar(page);
  const prompt = page.getByLabel("Prompt for phone");
  await expect(prompt).toBeVisible();

  await page.getByTestId("terminal").click();
  await expect(prompt).toBeFocused();
  await page.locator(".xterm-helper-textarea").focus();
  await page.keyboard.type("typed in terminal\n");

  await prompt.fill("hello from the bar");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(screen(page)).toContainText("got: hello from the bar");
  await expect(prompt).toHaveValue("");

  await page.getByRole("button", { name: "3", exact: true }).click();
  await page.getByRole("button", { name: "Enter", exact: true }).click();
  await expect(screen(page)).toContainText("got: 3");
  await expect(screen(page)).not.toContainText("typed in terminal");
});

test("scrolling still reaches tmux, and a prompt sent while scrolled back still arrives", async ({ page }) => {
  await withInputBar(page);
  const inMode = () => s.tmux("list-panes", "-a", "-F", "#{session_name} #{pane_in_mode}").split("\n").find((line) => line.startsWith("phone"));
  await page.getByTestId("terminal").hover();
  await page.mouse.wheel(0, -300);
  await expect.poll(inMode).toMatch(/ 1$/);
  await page.getByLabel("Prompt for phone").fill("from copy mode");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(screen(page)).toContainText("got: from copy mode");
  expect(inMode()).toMatch(/ 0$/);
});

test("the keyboard button switches back to typing in the terminal, and is remembered", async ({ page }) => {
  await withInputBar(page);
  await page.getByRole("button", { name: "Type in the terminal" }).click();
  await expect(page.getByLabel("Prompt for phone")).toHaveCount(0);
  await page.getByTestId("terminal").click();
  await page.keyboard.type("typed directly\n");
  await expect(screen(page)).toContainText("got: typed directly");

  await page.reload();
  await expect(page.getByRole("button", { name: /Input bar/ })).toBeVisible();
  await page.getByRole("button", { name: /Input bar/ }).click();
  await expect(page.getByLabel("Prompt for phone")).toBeVisible();
});

test("an unsent prompt is kept for its session", async ({ page }) => {
  await withInputBar(page);
  await page.getByLabel("Prompt for phone").fill("half written");
  await page.reload();
  await expect(page.getByLabel("Prompt for phone")).toHaveValue("half written");
});

test("^B then a key goes to tmux through the terminal: c makes a window", async ({ page }) => {
  await withInputBar(page);
  const windows = () => s.tmux("list-windows", "-t", s.tmux("list-sessions", "-F", "#{session_name}").split("\n").find((name) => name.startsWith("phone"))!).trim().split("\n").length;
  expect(windows()).toBe(1);
  const prefix = page.getByRole("button", { name: "tmux prefix, Control B" });
  await prefix.click();
  await expect(prefix).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("Prompt for phone").fill("c");
  await page.getByRole("button", { name: "Send" }).click();
  await expect.poll(windows).toBe(2);
  await expect(prefix).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByLabel("Prompt for phone")).toHaveValue("");
});

test("with a keyboard, Enter sends and Shift+Enter is a new line", async ({ page }) => {
  await withInputBar(page);
  const prompt = page.getByLabel("Prompt for phone");
  await expect(prompt).toHaveAttribute("placeholder", "Message phone");
  await prompt.click();
  await page.keyboard.type("first");
  await page.keyboard.press("Shift+Enter");
  await page.keyboard.type("second");
  await expect(prompt).toHaveValue("first\nsecond");
  await page.keyboard.press("Enter");
  await expect(screen(page)).toContainText("got: first");
  await expect(prompt).toHaveValue("");
});

test("reception holds a prompt that looks like new work, and clears first when asked", async ({ page }) => {
  await withInputBar(page);
  const SHOTS = process.env.RECEPTION_SHOTS;
  const reception = page.getByRole("button", { name: "Reception" });
  await expect(reception).toHaveAttribute("aria-pressed", "false");
  await reception.click();
  await expect(reception).toHaveAttribute("aria-pressed", "true");

  let answer = () => ({ id: "c1", verdict: "clear", reason: "The session is fixing the login flow; this asks for a deployment guide.", by: "test" });
  let hold: Promise<void> = Promise.resolve();
  await page.route("**/plugins/input/sessions/*/check", async (route) => {
    await hold;
    await route.fulfill({ json: answer() });
  });

  const prompt = page.getByLabel("Prompt for phone");
  let release = () => {};
  hold = new Promise((resolve) => (release = resolve));
  await prompt.fill("Please write the deployment guide for the new service, with rollback steps");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Checking it against the current work…")).toBeVisible();
  if (SHOTS) await page.locator(".input-bar").screenshot({ path: `${SHOTS}/checking.png` });
  release();

  await expect(page.getByText("This looks like new work")).toBeVisible();
  await expect(prompt).toHaveValue(/deployment guide/);
  if (SHOTS) await page.locator(".input-bar").screenshot({ path: `${SHOTS}/ask.png` });
  await page.getByRole("button", { name: "Clear context & send" }).click();
  await expect(screen(page)).toContainText("got: /clear");
  await expect(screen(page)).toContainText("got: Please write the deployment guide");
  await expect(page.getByText("Context cleared, then sent")).toBeVisible();
  await expect(prompt).toHaveValue("");

  await prompt.fill("Another long prompt that the check will hold back");
  await page.keyboard.press("Enter");
  await expect(page.getByText("This looks like new work")).toBeVisible();
  if (SHOTS) {
    await page.emulateMedia({ colorScheme: "dark" });
    await page.locator(".input-bar").screenshot({ path: `${SHOTS}/ask-dark.png` });
  }
  await page.keyboard.press("Escape");
  await expect(page.getByText("This looks like new work")).toHaveCount(0);
  await expect(prompt).toHaveValue("Another long prompt that the check will hold back");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Send as is" }).click();
  await expect(screen(page)).toContainText("got: Another long prompt");

  answer = () => ({ id: "c2", verdict: "keep", reason: "A follow-up.", by: "test" });
  await prompt.fill("And a follow-up the check keeps in the same context");
  await page.keyboard.press("Enter");
  await expect(screen(page)).toContainText("got: And a follow-up");
  await expect(page.getByText("This looks like new work")).toHaveCount(0);
});
