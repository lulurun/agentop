import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

let s: Service;

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("agentop.renderer", "dom");
    // As a phone would start: the bar shows.
    if (!localStorage.getItem("agentop.input.mode")) localStorage.setItem("agentop.input.mode", "bar");
  });
});

test.afterEach(async () => {
  await s.dispose();
});

/** A service with the Input bar on, which is off by default. */
async function withInputBar(page: Page) {
  s = new Service("alpha");
  mkdirSync(join(s.root, "state", "state"), { recursive: true });
  writeFileSync(join(s.root, "state", "state", "plugins.json"), JSON.stringify({ input: { enabled: true } }));
  await s.start();
  await page.goto(s.signedInUrl);
  await page.getByRole("button", { name: "New session" }).first().click();
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
