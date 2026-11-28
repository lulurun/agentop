import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, ServiceClient, type PluginInfo, type Session } from "../../api/client";
import type { PluginContext } from "../types";
import { DetailsPanel } from "../../components/DetailsPanel";
import { Workbench } from "../../components/Workbench";
import { useApp, layoutOf } from "../../store";
import { plugin as projectsPlugin } from "./index";
import { ProgressViewer } from "./ProgressViewer";
import { canOpen, chosenAfter, isProjectCoordinator, latestOnly, pick, progressPath, resultMeta, type Progress, type ResultFile, type TaskRow } from "./progress";

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
const plugin = {
  id: "projects",
  client: { get: (path: string) => new Promise((resolve, reject) => calls.push({ path, resolve, reject })) },
} as unknown as PluginContext;

function render(project = "alpha") {
  act(() => root.render(createElement(ProgressViewer, { key: project, plugin, project })));
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
}

const q = <T extends Element = HTMLElement>(selector: string) => container.querySelector<T & HTMLElement>(selector);
const byText = (selector: string, text: string) =>
  [...container.querySelectorAll<HTMLElement>(selector)].find((el) => el.textContent?.trim() === text);
const click = (el: HTMLElement | undefined | null) => act(() => el!.click());

beforeEach(() => {
  calls = [];
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("Projects workbench", () => {
  it("opens one closable Progress Viewer in the right area and keeps the side panel available", async () => {
    const original = useApp.getState();
    const feature = "plugin:projects";
    const info = { id: "projects", state: "enabled", ui: ["feature"] } as PluginInfo;
    const coordinator = session({ status: "running" });
    const worker = session({ id: "s2", display_name: "worker", status: "running" });
    const projectClient = { ...plugin, client: { ...plugin.client, get: async () => [{ name: "alpha", folder: "/alpha" }] } } as PluginContext;
    const client = vi.spyOn(ServiceClient.prototype, "plugin").mockReturnValue(plugin.client);
    const Right = projectsPlugin.feature!.right!;
    const Left = projectsPlugin.feature!.left!;
    const pane = (side: string) => container.querySelector<HTMLElement>(`[data-test-panel="${side}"]`)!;
    // Picking a project reads its layout.json first; this one has none.
    vi.stubGlobal("fetch", async (url: string) => new Response(null, { status: String(url).endsWith("/projects/alpha/layout") ? 204 : 404 }));
    try {
      act(() => useApp.setState({
        connections: [{ id: original.activeId, label: "Local", url: "http://localhost", token: "" }],
        features: { [original.activeId]: feature },
        layouts: {}, selected: {}, folded: [], plugins: [info], pluginsLoaded: true,
        sessions: { s1: coordinator, s2: worker },
        act: (async () => [coordinator, worker]) as typeof original.act,
      }));
      act(() => root.render(createElement("div", null,
        createElement("aside", { "data-test-panel": "left" }, createElement(Left, { plugin: projectClient })),
        createElement(Workbench, { feature, background: null, renderSession: () => "Terminal" }),
        createElement("aside", { "data-test-panel": "right" }, createElement(Right, { plugin: projectClient })),
      )));
      await settle();
      click(pane("left").querySelector<HTMLButtonElement>(".project-pick"));
      await settle();
      click(byText("button", "Progress Viewer"));
      click(byText("button", "Progress Viewer"));
      const rightArea = q('[data-area="right"]')!;
      expect(rightArea.querySelector('[role="tab"]')?.textContent).toBe("Progress Viewer: alpha");
      expect(layoutOf(useApp.getState()).right?.tabs).toHaveLength(1);
      expect(q('[data-area="left"]')?.textContent).toContain("Terminal");
      expect(pane("right").textContent).toContain("Coordinator");
      expect(pane("right").querySelector(".panel-section:last-child")?.getAttribute("aria-label")).toBe("Actions");
      for (const side of ["left", "right"]) expect(pane(side).querySelector('[role="tablist"]')).toBeNull();
      expect(calls.map((c) => c.path)).toEqual(["/projects/alpha/progress"]);
      calls[0].resolve(progress());
      await settle();
      expect(rightArea.textContent).toContain("Ship the viewer.");
      click(q('[aria-label="Close Progress Viewer: alpha"]'));
      expect(q('[data-area="right"]')).toBeNull();
      act(() => useApp.getState().select(worker.id));
      expect(byText("button", "Progress Viewer")).toBeUndefined();
      expect(pane("right").querySelector(".panel-section:last-child")?.getAttribute("aria-label")).toBe("Actions");
    } finally {
      act(() => root.render(null));
      act(() => useApp.setState(original));
      client.mockRestore();
      vi.unstubAllGlobals();
    }
  });

  it("ends shared session Details with Actions and retains Stop confirmation", () => {
    const original = useApp.getState();
    const running = session({ project: "default", display_name: "worker", status: "running" });
    try {
      act(() => useApp.setState({ features: {}, layouts: {}, folded: [], sessions: { s1: running }, selected: { [original.activeId]: "s1" } }));
      act(() => root.render(createElement(DetailsPanel)));
      const actions = q('section:last-child[aria-label="Actions"]')!;
      click(byText("button", "Stop session"));
      expect(actions.textContent).toContain("Stop worker?");
      click(byText("button", "Cancel"));
      expect(actions.textContent).toContain("Stop session");
      act(() => useApp.setState({ sessions: { s1: { ...running, managed: false } } }));
      expect(byText("button", "Stop session")).toBeUndefined();
    } finally {
      act(() => root.render(null));
      act(() => useApp.setState(original));
    }
  });
});

describe("Progress Viewer", () => {
  it("renders the direction, plan and execution groups in order", async () => {
    render();
    expect(container.textContent).toContain("Loading the track");
    calls[0].resolve(progress());
    await settle();
    expect(container.textContent).toContain("Ship the viewer.");
    expect(container.textContent).toContain("M1 Build");
    expect([...container.querySelectorAll(".progress-id")].map((el) => el.textContent)).toEqual([
      "P2_M1A1-3", "P2_M1A1-2", "P2_M1A1-1", "M1A1 note",
    ]);
    expect(container.textContent).toContain("Awaiting review (blocked)");
    expect(container.textContent).toContain("by owner");
  });

  it("expands a task's input, output and evaluation and keeps it open across refresh", async () => {
    render();
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
    render();
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

  it("opens a fresh view per project and drops a pending answer from the previous one", async () => {
    render();
    render("beta");
    expect(calls.map((c) => c.path)).toEqual(["/projects/alpha/progress", "/projects/beta/progress"]);
    calls[0].resolve(progress({ direction: "STALE" }));
    await settle();
    expect(container.textContent).not.toContain("STALE");
    expect(container.textContent).toContain("Loading the track");
    calls[1].resolve(progress({ project: "alpha", direction: "WRONG PROJECT" }));
    await settle();
    expect(container.textContent).not.toContain("WRONG PROJECT");
  });

  it("retries after an error, keeps a stale snapshot on a failed refresh, and clears a missing project", async () => {
    render();
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
    render();
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

  it("lists each action's results beneath it and opens a text result in this project's view", async () => {
    const original = useApp.getState();
    const openTabIn = vi.fn();
    act(() => useApp.setState({ openTabIn }));
    try {
      render();
      const file = (fields: Partial<ResultFile>): ResultFile =>
        ({ name: "x", path: "results/x", task: null, kind: "supporting", size: 10, problem: null, ...fields });
      calls[0].resolve(progress({
        actions: [
          { ref: "M1A1", title: "Build it", status: "done", results: [
            file({ name: "P2_M1A1_dev.md", path: "results/P2_M1A1_dev.md", task: "P2_M1A1-1", kind: "artifact", size: 2048 }),
            file({ name: "P2_M1A1_tests.txt", path: "results/P2_M1A1_tests.txt" }),
            file({ name: "P2_M1A1_link.txt", path: "results/P2_M1A1_link.txt", problem: "outside", size: null }),
          ] },
          { ref: "M1A10", title: null, status: null, results: [file({ name: "P2_M1A10_a.md", path: "results/P2_M1A10_a.md", task: "P2_M1A10-1", kind: "artifact", problem: "missing", size: null })] },
          { ref: "M1A2", title: "Later", status: "planned", results: [], more: 0 },
        ],
      }));
      await settle();
      const first = q('[aria-label="Results of M1A1"]')!;
      expect([...first.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
        "P2_M1A1_dev.mdartifact of P2_M1A1-1 · 2 KB",
        "P2_M1A1_tests.txtsupporting · 10 B",
        "P2_M1A1_link.txtsupporting · leads outside the project, not shown",
      ]);
      expect(first.querySelectorAll("button")).toHaveLength(2);
      expect(q('[aria-label="Results of M1A10"]')!.textContent).toContain("not written");
      expect(q('[aria-label="Results of M1A10"] button')).toBeNull();
      expect(container.textContent).toContain("No results yet.");

      click(byText("button", "P2_M1A1_tests.txt"));
      expect(openTabIn).toHaveBeenCalledWith(
        { connection: original.activeId, feature: "plugin:projects", project: "alpha" },
        { kind: "file", file: { sessionId: "alpha", source: "project", path: "results/P2_M1A1_tests.txt" } },
        "right",
        "replace",
      );
    } finally {
      act(() => useApp.setState({ openTabIn: original.openTabIn }));
    }
  });

  it("says why a result can't be opened", () => {
    const base: ResultFile = { name: "a", path: "results/a", task: null, kind: "supporting", size: 0, problem: "empty" };
    expect([canOpen(base), resultMeta(base)]).toEqual([true, "supporting · empty"]);
    for (const problem of ["missing", "outside", "folder", "unsupported"] as const) expect(canOpen({ ...base, problem })).toBe(false);
    expect(resultMeta({ ...base, problem: null, size: 3 * 1024 * 1024 })).toBe("supporting · 3.0 MB");
  });
});
