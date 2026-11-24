import { expect, type Page } from "@playwright/test";

/**
 * Start a session from Sessions' start buffer. The services' agy isn't a model, so the probe
 * fails and the row asks for the folder: the test gives it, with the agent and name. It returns once
 * the prompt has been typed in, which the service does once the tool is up, so a test's own typing
 * never lands in the middle of it.
 */
export async function startFromPrompt(
  page: Page,
  { cwd, name, tool = "claude", prompt = `work on ${name}`, wait = true }: { cwd: string; name: string; tool?: string; prompt?: string; wait?: boolean },
) {
  await page.getByRole("main").getByRole("tab", { name: "New", exact: true }).click();
  await page.getByPlaceholder("What should the new session do?").fill(prompt);
  await page.getByRole("button", { name: "Start a session with this prompt" }).click();
  const row = page.locator(".start-row").first();
  await row.getByLabel("Folder").fill(cwd);
  await row.getByLabel("Agent").selectOption(tool);
  await row.getByLabel("Name").fill(name);
  await row.getByRole("button", { name: "Launch", exact: true }).click();
  if (!wait) return;
  await expect(page.locator(".tab-pane:not([hidden]) [data-testid=terminal] .xterm-rows").first()).toContainText(prompt, { timeout: 30_000 });
}
