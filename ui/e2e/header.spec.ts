import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

const services: Service[] = [];

test.afterEach(async () => {
  await Promise.all(services.splice(0).map((s) => s.dispose()));
});

function layoutFile(s: Service) {
  const path = join(s.root, "state", "projects", "default", ".project", "layout.json");
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : null;
}

async function shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.AGENTOP_E2E_SHOTS;
  await page.screenshot({ path: dir ? join(dir, `${name}.png`) : info.outputPath(`${name}.png`) });
}

const LAYOUT = ["Settings", "Toggle left panel", "Toggle left area", "Toggle right area", "Toggle right panel"];

/** The header's controls, left to right: each one's box, which must not overlap the next and must be on screen. */
async function headerOrder(page: Page) {
  const header = page.locator(".header");
  const tools = header.getByRole("group", { name: "Settings and layout" });
  const parts: [string, Locator][] = [
    ["brand", header.getByRole("img", { name: "agentop" })],
    ["connection", header.locator(".switcher-current")],
    ["features", header.locator(".features:visible, .features-compact:visible").first()],
    ...LAYOUT.map((name): [string, Locator] => [name, tools.getByRole("button", { name, exact: true })]),
  ];
  const width = page.viewportSize()!.width;
  let right = -1;
  for (const [name, part] of parts) {
    await expect(part, name).toBeVisible();
    const box = (await part.boundingBox())!;
    expect(box.x, `${name} after the one before`).toBeGreaterThanOrEqual(right);
    expect(box.x + box.width, `${name} on screen`).toBeLessThanOrEqual(width);
    right = box.x + box.width;
  }
  // The settings and the toggles sit at the right end.
  const end = (await tools.boundingBox())!;
  expect(width - (end.x + end.width)).toBeLessThanOrEqual(12);
  expect(await tools.getByRole("button").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")))).toEqual(LAYOUT);
}

/** Sessions with two areas: the start buffer on the left, Status on the right. */
async function twoAreas(page: Page) {
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: /status$/ }).click();
  await page.getByRole("tab", { name: "Status" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the right area" }).click();
  await expect(page.locator(".workbench[data-areas='2']")).toBeVisible();
}

test("the header holds the brand, connection and features on the left, and settings with the four toggles on the right", async ({ page }, info) => {
  const s = await new Service("alpha").start();
  services.push(s);
  await page.goto(s.signedInUrl);
  await expect(page.locator(".link-connected")).toBeVisible();
  await headerOrder(page);
  const button = (name: string) => page.getByRole("button", { name, exact: true });

  // One area: nothing to hide beside it, so both area toggles are disabled; the left one shows as on.
  await expect(button("Toggle left area")).toBeDisabled();
  await expect(button("Toggle left area")).toHaveAttribute("aria-pressed", "true");
  await expect(button("Toggle right area")).toBeDisabled();
  await expect(button("Toggle right area")).toHaveAttribute("aria-pressed", "false");
  // The tab bar has no split button any more.
  await expect(page.locator(".tab-split")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /area too$/ })).toHaveCount(0);
  await shot(page, info, "desktop-one-area");

  // The panel toggles: pressed state, the click, and their keys.
  await expect(button("Toggle left panel")).toHaveAttribute("aria-pressed", "true");
  await button("Toggle left panel").click();
  await expect(page.locator(".sidebar")).toHaveCount(0);
  await expect(button("Toggle left panel")).toHaveAttribute("aria-pressed", "false");
  await page.keyboard.press("Control+Shift+Digit1");
  await expect(page.locator(".sidebar")).toBeVisible();
  await button("Toggle right panel").click();
  await expect(page.locator(".details")).toHaveCount(0);
  await expect.poll(() => layoutFile(s)?.["right-panel"]?.open).toBe(false);
  await page.keyboard.press("Control+Shift+Digit0");
  await expect(page.locator(".details")).toBeVisible();

  // Two areas: each can be hidden while the other shows, its tabs kept in the layout file.
  await twoAreas(page);
  await expect(button("Toggle left area")).toBeEnabled();
  await expect(button("Toggle right area")).toHaveAttribute("aria-pressed", "true");
  await shot(page, info, "desktop-two-areas");
  await button("Toggle left area").click();
  const left = page.locator('.workbench[data-feature="sessions"] .tab-group[data-area="left"]');
  const right = page.locator('.workbench[data-feature="sessions"] .tab-group[data-area="right"]');
  await expect(left).toHaveCount(0);
  await expect(right).toBeVisible();
  await expect(page.locator(".workbench[data-areas='1']")).toBeVisible();
  await expect(button("Toggle left area")).toHaveAttribute("aria-pressed", "false");
  // The right area is the only one shown now, so it can't be hidden.
  await expect(button("Toggle right area")).toBeDisabled();
  await expect.poll(() => layoutFile(s)?.["mid-panel"]?.left).toMatchObject({ open: false, tabs: [{ content: { kind: "start" } }] });
  expect(layoutFile(s).focused).toBe("right");
  await shot(page, info, "desktop-left-area-hidden");

  // Kept after a reload, and opened again by its toggle.
  await page.reload();
  await expect(right.getByRole("tab", { name: "Status" })).toBeVisible();
  await expect(left).toHaveCount(0);
  await button("Toggle left area").click();
  await expect(left.getByRole("tab", { name: "New", exact: true })).toBeVisible();
  await expect.poll(() => layoutFile(s)?.["mid-panel"]?.left?.open).toBe(true);

  // Ctrl+Shift+W focuses the left area, opening it if it was hidden.
  await button("Toggle left area").click();
  await expect(left).toHaveCount(0);
  await page.keyboard.press("Control+Shift+KeyW");
  await expect(left).toBeVisible();
  await expect(left).toHaveAttribute("data-focused", "true");
});

for (const [label, size] of [["tablet", { width: 760, height: 900 }], ["phone", { width: 375, height: 760 }]] as const) {
  test(`on a ${label} the header fits, every toggle works, and the area toggles pick the area shown`, async ({ page }, info) => {
    const s = await new Service("a-service-with-a-long-name").start();
    services.push(s);
    await page.setViewportSize(size);
    await page.goto(s.signedInUrl);
    await expect(page.locator(".link-connected")).toBeVisible();
    await headerOrder(page);
    const button = (name: string) => page.getByRole("button", { name, exact: true });

    // The side panels are overlays here.
    await expect(page.locator(".sidebar")).toHaveCount(0);
    await button("Toggle left panel").click();
    await expect(page.locator(".sidebar")).toBeVisible();
    await shot(page, info, `${label}-left-panel`);
    await page.locator(".scrim").click({ position: { x: size.width - 10, y: 200 } });
    await expect(page.locator(".sidebar")).toHaveCount(0);
    await button("Toggle right panel").click();
    await expect(page.locator(".details")).toBeVisible();
    await button("Toggle right panel").click();
    await expect(page.locator(".details")).toHaveCount(0);

    // Two areas, one drawn: the right one is active after the move; its toggle is on, the left one offers to show the left.
    await twoAreas(page);
    const left = page.locator('.workbench[data-feature="sessions"] .tab-group[data-area="left"]');
    const right = page.locator('.workbench[data-feature="sessions"] .tab-group[data-area="right"]');
    await expect(right).toBeVisible();
    await expect(left).toBeHidden();
    // The one area shown fills the middle panel.
    expect((await right.boundingBox())!.width).toBeGreaterThanOrEqual(size.width - 4);
    await expect(button("Toggle right area")).toHaveAttribute("aria-pressed", "true");
    await expect(button("Toggle right area")).toBeDisabled();
    await expect(button("Toggle left area")).toHaveAttribute("aria-pressed", "false");
    await headerOrder(page);
    await shot(page, info, `${label}-right-area`);
    await button("Toggle left area").click();
    await expect(left).toBeVisible();
    await expect(right).toBeHidden();
    expect((await left.boundingBox())!.width).toBeGreaterThanOrEqual(size.width - 4);
    await expect(button("Toggle left area")).toHaveAttribute("aria-pressed", "true");
    await expect(button("Toggle right area")).toBeEnabled();
    await shot(page, info, `${label}-left-area`);

    // The connection and feature menus still open.
    await page.locator(".switcher-current").click();
    await expect(page.getByRole("menuitem", { name: /status$/ })).toBeVisible();
    await shot(page, info, `${label}-connection-menu`);
    await page.keyboard.press("Escape");
  });
}
