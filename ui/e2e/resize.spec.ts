import { expect, test, type Locator, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Service } from "./service";

const services: Service[] = [];

test.afterEach(async () => {
  await Promise.all(services.splice(0).map((s) => s.dispose()));
});

const width = async (locator: Locator) => (await locator.boundingBox())!.width;

/** Drag a separator by `dx` pixels with the mouse, in steps as a hand would. */
async function drag(page: Page, separator: Locator, dx: number) {
  const box = (await separator.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y, { steps: 8 });
  await page.mouse.up();
}

/** Sessions' layout.json on the test's own service, once written. */
function layoutFile(s: Service) {
  const path = join(s.root, "state", "projects", "default", ".project", "layout.json");
  return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : null;
}

/** Sessions with two areas: the start buffer on the left, Status on the right. */
async function twoAreas(page: Page) {
  await page.locator(".switcher-current").click();
  await page.getByRole("menuitem", { name: /status$/ }).click();
  await page.getByRole("tab", { name: "Status" }).click({ button: "right" });
  await page.getByRole("menuitem", { name: "Move to the right area" }).click();
  await expect(page.locator(".workbench[data-areas='2']")).toBeVisible();
}

test("the side panels and the two areas are resized by dragging their boundaries, and kept in the project's layout", async ({ page }) => {
  const s = await new Service("alpha").start();
  services.push(s);
  await page.goto(s.signedInUrl);
  const sidebar = page.locator(".sidebar");
  const details = page.locator(".details");
  const leftEdge = page.getByRole("separator", { name: "Resize the left panel" });
  const rightEdge = page.getByRole("separator", { name: "Resize the right panel" });
  await expect(sidebar).toBeVisible();
  expect(await width(sidebar)).toBe(256);
  expect(await width(details)).toBe(312);

  await drag(page, leftEdge, 100);
  expect(await width(sidebar)).toBe(356);
  await drag(page, rightEdge, -80);
  expect(await width(details)).toBe(392);
  await expect(page.locator("body[data-resizing]")).toHaveCount(0);

  // The keyboard moves them too.
  await leftEdge.focus();
  await page.keyboard.press("ArrowLeft");
  expect(await width(sidebar)).toBe(340);
  await expect(leftEdge).toHaveAttribute("aria-valuenow", "340");

  // A boundary can't be dragged past its bounds: the side keeps 180px, the middle panel 320px.
  await drag(page, leftEdge, -600);
  expect(await width(sidebar)).toBe(180);
  await drag(page, leftEdge, 2000);
  expect(await width(page.locator(".main"))).toBeGreaterThanOrEqual(320);
  await leftEdge.dblclick();
  expect(await width(sidebar)).toBe(256);
  await drag(page, leftEdge, 64);

  // The areas' shared boundary.
  await twoAreas(page);
  const left = page.locator(".tab-group[data-area='left']");
  const right = page.locator(".tab-group[data-area='right']");
  const split = page.getByRole("separator", { name: "Resize the left and right areas" });
  const before = await width(left);
  // Each area keeps 200px.
  await drag(page, split, -1000);
  expect(Math.abs((await width(left)) - 200)).toBeLessThanOrEqual(1);
  await split.dblclick();
  expect(Math.abs((await width(left)) - before)).toBeLessThanOrEqual(1);
  await drag(page, split, -100);
  expect(Math.abs((await width(left)) - (before - 100))).toBeLessThanOrEqual(1);
  await split.focus();
  await page.keyboard.press("Shift+ArrowRight");
  expect(Math.abs((await width(left)) - (before - 100 + 64))).toBeLessThanOrEqual(1);
  const share = (await width(left)) / ((await width(left)) + (await width(right)));
  await page.screenshot({ path: "test-results/resize-desktop.png" });

  await expect.poll(() => layoutFile(s)?.["left-panel"].width, { timeout: 5000 }).toBe(320);
  expect(layoutFile(s)["right-panel"].width).toBe(392);
  expect(layoutFile(s)["mid-panel"].split).toBeCloseTo(share, 2);

  // All of it comes back from the file.
  await page.reload();
  await expect(right).toBeVisible();
  expect(await width(sidebar)).toBe(320);
  expect(await width(details)).toBe(392);
  expect(Math.abs((await width(left)) / ((await width(left)) + (await width(right))) - share)).toBeLessThan(0.01);

  // Shutting a panel hides its boundary and gives its room to the middle; opening it brings back its width.
  const middle = await width(page.locator(".main"));
  await page.getByRole("button", { name: "Toggle left panel" }).click();
  await expect(leftEdge).toHaveCount(0);
  expect(await width(page.locator(".main"))).toBe(middle + 320);
  await page.getByRole("button", { name: "Toggle left panel" }).click();
  expect(await width(sidebar)).toBe(320);

  // A narrower window narrows the sides as drawn but keeps what was set.
  await page.setViewportSize({ width: 1000, height: 820 });
  await expect.poll(() => width(page.locator(".main"))).toBeGreaterThanOrEqual(320);
  expect(await width(sidebar)).toBeLessThan(320);
  expect(await width(sidebar)).toBeGreaterThanOrEqual(180);
  await page.setViewportSize({ width: 1360, height: 820 });
  await expect.poll(() => width(sidebar)).toBe(320);

  // Below 900px the panels are overlays of their own width, with no boundaries to drag, and one area shows.
  await page.setViewportSize({ width: 800, height: 820 });
  await expect(page.getByRole("separator")).toHaveCount(0);
  // This browser's own open state shows there, not the project's.
  if (!(await sidebar.isVisible())) await page.getByRole("button", { name: "Toggle left panel" }).click();
  await expect(sidebar).toBeVisible();
  expect(await width(sidebar)).toBe(300);
  await page.screenshot({ path: "test-results/resize-narrow.png" });
  await page.setViewportSize({ width: 1360, height: 820 });
  expect(layoutFile(s)["left-panel"].width).toBe(320);
  await expect.poll(() => width(sidebar)).toBe(320);
});

test("a feature without a project keeps its widths in the browser, apart from the projects'", async ({ page }) => {
  const s = await new Service("beta").start();
  services.push(s);
  await page.goto(s.signedInUrl);
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toBeVisible();
  await page.getByRole("button", { name: "Projects" }).click();
  // Projects with none picked belongs to no project.
  const leftEdge = page.getByRole("separator", { name: "Resize the left panel" });
  await drag(page, leftEdge, 60);
  expect(await width(sidebar)).toBe(316);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("agentop.layout")!).leftWidth)).toBe(316);

  await page.getByRole("button", { name: "Sessions" }).click();
  expect(await width(sidebar)).toBe(256);
  await page.getByRole("button", { name: "Projects" }).click();
  expect(await width(sidebar)).toBe(316);
});
