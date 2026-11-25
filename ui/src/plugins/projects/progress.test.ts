import { act, createElement, useEffect, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ApiError, type Session } from "../../api/client";
import type { PluginContext } from "../types";
import { ProgressPanels } from "./index";
import { chosenAfter, isProjectCoordinator, latestOnly, pick, progressPath, type Progress, type TaskRow } from "./progress";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function task(id: string, fields: Partial<TaskRow> = {}): TaskRow {
  return {
    id, generation: 2, action: "M1A1", unlinked: false, session: "dev", tool: "claude", model: null, status: "accepted",
    state: "accepted", blocked: false, opened_at: 1_700_000_000, opened_approx: false, closed_at: 1_700_000_100,
    closed_approx: false, input: { goal: `${id} goal`, accept: ["tests pass"], artifact: "results/dev.md", brief: null },
    rounds: [{ round: 1, summary: `${id} summary`, feedback: null }], checked: "ran it", note: null, sources: ["markdown"],
    raw: null, ...fields,
  };
}

function progress(fields: Partial<Progress> = {}): Progress {
  return {
    project: "alpha", generations: [2, 1], current: 2, generation: 2, fallback: false,
    direction: "# Direction 2\n\nShip the viewer.", direction_placeholder: false, plan: "# Plan 2\n\n## M1 Build  [in progress]",
    active: [task("P2_M1A1-3", { status: "open", state: "awaiting review", blocked: true, closed_at: null })],
    completed: [task("P2_M1A1-2"), task("P2_M1A1-1", { status: "failed", state: "failed", note: "never started" })],
    notes: [{ id: "P2_M1A1 note 1", action: "M1A1", by: "owner", recorded: "2026-11-25 10:00", recorded_at: 1_700_000_000, body: "Answered" }],
    issues: [], missing: { track: false, direction: false, plan: false, executions: false }, legacy: false, ...fields,
  };
}

const session = (fields: Partial<Session> = {}) =>
  ({ id: "s1", managed: true, display_name: "Coordinator", project: "alpha", ...fields }) as Session;

describe("helpers", () => {
  it("encodes the project and asks for a direction only when one is picked", () => {
    expect(progressPath("a b/c", null)).toBe("/projects/a%20b%2Fc/progress");
    expect(progressPath("alpha", 10)).toBe("/projects/alpha/progress?generation=10");
  });

  it("offers the viewer only for the chosen, listed project's managed Coordinator outside History", () => {
    const listed = [{ name: "alpha" }];
    expect(isProjectCoordinator(session(), "alpha", listed, undefined)).toBe(true);
    expect(isProjectCoordinator(session({ display_name: "dev" }), "alpha", listed, undefined)).toBe(false);
    expect(isProjectCoordinator(session({ project: "beta" }), "alpha", listed, undefined)).toBe(false);
    expect(isProjectCoordinator(session({ managed: false }), "alpha", listed, undefined)).toBe(false);
    expect(isProjectCoordinator(session(), "alpha", listed, "past-1")).toBe(false);
    expect(isProjectCoordinator(session(), "alpha", [{ name: "beta" }], undefined)).toBe(false);
    expect(isProjectCoordinator(session(), undefined, listed, undefined)).toBe(false);
    expect(isProjectCoordinator(undefined, "alpha", listed, undefined)).toBe(false);
  });

  it("follows a new current direction, keeps a picked one, and follows again when it is gone", () => {
    expect(chosenAfter({ follow: true, generation: 2 }, progress({ current: 3, generation: 3 }))).toEqual({ follow: true, generation: 3 });
    expect(chosenAfter(pick(1, 2), progress({ current: 3, generation: 1 }))).toEqual({ follow: false, generation: 1 });
    expect(chosenAfter(pick(1, 2), progress({ current: 3, generation: 3, fallback: true }))).toEqual({ follow: true, generation: 3 });
    expect(pick(2, 2).follow).toBe(true);
  });

  it("uses only the newest request's answer", () => {
    const requests = latestOnly();
    const first = requests.begin();
    const second = requests.begin();
    expect([first(), second()]).toEqual([false, true]);
    requests.cancel();
    expect(second()).toBe(false);
  });
});

interface Pending {
  path: string;
  resolve(value: unknown): void;
  reject(error: unknown): void;
}

let container: HTMLDivElement;
let root: Root;
let calls: Pending[];
let detailsMounts: number;
const plugin = {
  id: "projects",
  client: { get: (path: string) => new Promise((resolve, reject) => calls.push({ path, resolve, reject })) },
} as unknown as PluginContext;

function Details({ entry }: { entry: ReactNode }) {
  useEffect(() => {
    detailsMounts++;
  }, []);
  return createElement("div", { className: "fake-details" }, "Details body", entry);
}

function render(viewerKey: string | null, project = "alpha") {
  act(() => {
    root.render(createElement(ProgressPanels, { plugin, viewerKey, project, details: (entry: ReactNode) => createElement(Details, { entry }) }));
  });
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T & HTMLElement>(selector);
const byText = (selector: string, text: string) =>
  [...container.querySelectorAll<HTMLElement>(selector)].find((el) => el.textContent?.trim() === text);
const tabs = () => [...container.querySelectorAll('[role="tab"]')].map((t) => t.textContent);
const click = (el: HTMLElement | undefined | null) => act(() => el!.click());
const visible = () => container.querySelector('[role="tabpanel"]:not([hidden])')?.textContent ?? "";

beforeEach(() => {
  calls = [];
  detailsMounts = 0;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ProgressPanels", () => {
  it("has no entry for anything but a Coordinator", () => {
    render(null);
    expect(byText("button", "Progress Viewer")).toBeUndefined();
    expect(tabs()).toEqual([]);
    expect(container.textContent).toContain("Details body");
  });

  it("opens one closable tab, switches to Details and back, and closes to the entry", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    expect(tabs()).toEqual(["Details", "Progress Viewer"]);
    expect(q('[role="tab"][aria-selected="true"]')!.textContent).toBe("Progress Viewer");
    expect(calls.map((c) => c.path)).toEqual(["/projects/alpha/progress"]);
    expect(container.textContent).toContain("Loading the track");
    calls[0].resolve(progress());
    await settle();
    const shown = visible();
    expect(shown).toContain("Ship the viewer.");
    expect(shown).toContain("M1 Build");
    const ids = [...container.querySelectorAll(".progress-id")].map((el) => el.textContent);
    expect(ids).toEqual(["P2_M1A1-3", "P2_M1A1-2", "P2_M1A1-1", "M1A1 note"]);
    expect(shown).toContain("Awaiting review (blocked)");
    expect(shown).toContain("by owner");

    click(byText('[role="tab"]', "Details"));
    expect(visible()).toContain("Details body");
    click(byText(".fake-details button", "Progress Viewer"));
    expect(tabs()).toEqual(["Details", "Progress Viewer"]);
    expect(calls).toHaveLength(1);

    click(q('[aria-label="Close Progress Viewer"]'));
    expect(tabs()).toEqual([]);
    expect(document.activeElement?.textContent).toBe("Progress Viewer");
    expect(detailsMounts).toBe(1);
  });

  it("switches tabs from the keyboard", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    const list = q('[role="tablist"]')!;
    act(() => list.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(q('[role="tab"][aria-selected="true"]')!.textContent).toBe("Details");
    expect(document.activeElement?.textContent).toBe("Details");
  });

  it("expands a task's input, output and evaluation and keeps it open across refresh", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    calls[0].resolve(progress());
    await settle();
    const row = () => [...container.querySelectorAll<HTMLElement>(".progress-row")].find((b) => b.textContent?.includes("P2_M1A1-1"))!;
    click(row());
    expect(row().getAttribute("aria-expanded")).toBe("true");
    for (const part of ["P2_M1A1-1 goal", "tests pass", "results/dev.md", "P2_M1A1-1 summary", "Round 1 failed", "never started"]) {
      expect(container.textContent).toContain(part);
    }
    click(byText("button", "Refresh"));
    expect(byText("button", "Refreshing…")).toBeDefined();
    calls[1].resolve(progress());
    await settle();
    expect(row().getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps a picked direction on refresh and ignores an older request's late answer", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    calls[0].resolve(progress());
    await settle();
    const select = q<HTMLSelectElement>("select")!;
    act(() => {
      select.value = "1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(calls[1].path).toBe("/projects/alpha/progress?generation=1");
    act(() => {
      select.value = "2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    calls[2].resolve(progress());
    await settle();
    calls[1].resolve(progress({ generation: 1, direction: "OLD DIRECTION", active: [], completed: [], notes: [] }));
    await settle();
    expect(container.textContent).not.toContain("OLD DIRECTION");

    act(() => {
      select.value = "1";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    calls[3].resolve(progress({ generation: 1, direction: "Direction one", active: [], completed: [], notes: [] }));
    await settle();
    click(byText("button", "Refresh"));
    expect(calls[4].path).toBe("/projects/alpha/progress?generation=1");
  });

  it("closes on another session, project or service and drops the pending answer", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    render("svc/alpha/s2");
    expect(tabs()).toEqual([]);
    click(byText("button", "Progress Viewer"));
    expect(calls).toHaveLength(2);
    calls[0].resolve(progress({ direction: "STALE" }));
    await settle();
    expect(container.textContent).not.toContain("STALE");
    expect(container.textContent).toContain("Loading the track");
    calls[1].resolve(progress({ project: "beta", direction: "OTHER PROJECT" }));
    await settle();
    expect(container.textContent).not.toContain("OTHER PROJECT");
    render("other-svc/alpha/s2");
    expect(tabs()).toEqual([]);
    render(null);
    expect(byText("button", "Progress Viewer")).toBeUndefined();
  });

  it("retries after an error, keeps a stale snapshot on a failed refresh, and clears a missing project", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    calls[0].reject(new Error("network down"));
    await settle();
    expect(container.textContent).toContain("network down");
    click(byText("button", "Retry"));
    calls[1].resolve(progress());
    await settle();
    click(byText("button", "Refresh"));
    calls[2].reject(new Error("timeout"));
    await settle();
    expect(container.textContent).toContain("Couldn't refresh, so this may be out of date: timeout");
    expect(container.textContent).toContain("Ship the viewer.");
    click(byText("button", "Refresh"));
    calls[3].reject(new ApiError(404, "No such project"));
    await settle();
    expect(container.textContent).toContain("This project is no longer there.");
    expect(container.textContent).not.toContain("Ship the viewer.");
  });

  it("shows empty and missing states", async () => {
    render("svc/alpha/s1");
    click(byText("button", "Progress Viewer"));
    calls[0].resolve(progress({
      generations: [1], current: 1, generation: 1, direction_placeholder: true, plan: null, active: [], completed: [], notes: [],
      issues: ["track/tasks.jsonl has 1 unreadable line; skipped"], missing: { track: false, direction: false, plan: true, executions: true },
    }));
    await settle();
    expect(q("select")).toBeNull();
    for (const part of ["The direction isn't written yet.", "PLAN.md is missing from track/1.", "No execution recorded", "1 unreadable line"]) {
      expect(container.textContent).toContain(part);
    }
    click(byText("button", "Refresh"));
    calls[1].resolve(progress({ generations: [], current: 0, generation: 0, missing: { track: true, direction: true, plan: true, executions: true }, legacy: true }));
    await settle();
    expect(container.textContent).toContain("No track yet: this project keeps its state outside track/.");
  });
});
