import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "./connections";
import { layoutOf, panelsOf, parseLayouts, selectedId, useApp } from "./store";
import { areasOf, type TabContent } from "./tabs";

const PROJECTS = "plugin:projects" as const;
const DIALOGUES = "plugin:dialogue" as const;

/** Each connection's service: its projects' layout.json files, and replies held back when asked. */
const services = new Map<string, Map<string, unknown>>();
const requests: string[] = [];
let holdReads: ((url: string) => boolean) | null = null;
const held: (() => void)[] = [];

function serviceAt(url: string) {
  if (!services.has(url)) services.set(url, new Map());
  return services.get(url)!;
}

beforeEach(() => {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const { origin, pathname } = new URL(url);
    const method = init?.method ?? "GET";
    const match = pathname.match(/^\/api\/v2\/projects\/([^/]+)\/layout$/);
    requests.push(`${method} ${origin}${pathname}`);
    if (!match) return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    const files = serviceAt(origin);
    const project = decodeURIComponent(match[1]);
    if (method === "PUT") {
      files.set(project, JSON.parse(String(init.body)));
      return new Response(null, { status: 204 });
    }
    if (holdReads?.(`${origin}/${project}`)) await new Promise<void>((resolve) => held.push(resolve));
    const found = files.get(project);
    return found === undefined ? new Response(null, { status: 204 }) : new Response(JSON.stringify(found), { status: 200 });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  holdReads = null;
  held.splice(0).forEach((r) => r());
  requests.length = 0;
});

let n = 0;
/** A fresh connection, so the store's per-connection memory of earlier tests doesn't count. */
function connect(feature: string = PROJECTS): { id: string; url: string } {
  n += 1;
  const id = `c${n}`;
  const url = `http://svc${n}.test`;
  const connection = { id, url, label: id, token: "t" } as Connection;
  useApp.setState((s) => ({
    connections: [...s.connections, connection],
    activeId: id,
    features: { ...s.features, [id]: feature as never },
    sessions: {},
    sessionsLoaded: false,
    plugins: [],
    pluginsLoaded: false,
  }));
  return { id, url };
}

const file = (path: string, sessionId = "dev-1"): TabContent => ({ kind: "file", file: { sessionId, source: "workdir", path } });
const shownFiles = () =>
  areasOf(layoutOf(useApp.getState())).flatMap(([, g]) => g.tabs.map((t) => (t.content.kind === "file" ? t.content.file.path : t.content.kind)));
const until = async (check: () => boolean, ms = 2000) => {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise((r) => setTimeout(r, 5));
  }
};
const saved = (url: string, project: string) => serviceAt(new URL(url).origin).get(project) as Record<string, any> | undefined;

describe("project layouts", () => {
  it("keep each project's buffers to itself: A, then B, then A again", async () => {
    const { url } = connect();
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("x.py"), "left");
    app().openTab(file("notes.md"), "right", "tab");
    app().focusArea(PROJECTS, "left");
    app().toggleRight();
    const before = layoutOf(app());

    await app().setProject(PROJECTS, "beta");
    expect(shownFiles()).toEqual([]);
    expect(panelsOf(app()).rightOpen).toBe(true);
    app().openTab(file("y.py"), "left");
    expect(shownFiles()).toEqual(["y.py"]);

    // Switching flushed alpha's edits to its file at once.
    expect(saved(url, "alpha")?.["mid-panel"].left.tabs[0].content.file.path).toBe("x.py");
    await app().setProject(PROJECTS, "alpha");
    expect(layoutOf(app())).toEqual(before);
    expect(shownFiles()).toEqual(["x.py", "notes.md"]);
    expect(panelsOf(app()).rightOpen).toBe(false);
    await until(() => saved(url, "beta") !== undefined);
    expect(saved(url, "beta")?.["mid-panel"].left.tabs.map((t: any) => t.content.file.path)).toEqual(["y.py"]);
  });

  it("read the file again on entry, so an edit by hand shows", async () => {
    const { url } = connect();
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("x.py"), "left");
    await app().setProject(PROJECTS, "beta");
    const edited = structuredClone(saved(url, "alpha"))!;
    edited["mid-panel"].left.tabs[0].content.file.path = "by-hand.py";
    edited["left-panel"].open = false;
    serviceAt(new URL(url).origin).set("alpha", edited);
    await app().setProject(PROJECTS, "alpha");
    expect(shownFiles()).toEqual(["by-hand.py"]);
    expect(panelsOf(app()).leftOpen).toBe(false);
  });

  it("show the defaults for a missing or broken file, and don't write over a newer app's", async () => {
    const { url } = connect();
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("broken", { version: 1, "mid-panel": "nope", focused: 7 });
    await app().setProject(PROJECTS, "broken");
    expect(shownFiles()).toEqual([]);
    const newer = { version: 9, whatever: true };
    serviceAt(new URL(url).origin).set("future", newer);
    await app().setProject(PROJECTS, "future");
    app().openTab(file("x.py"), "left");
    expect(shownFiles()).toEqual(["x.py"]);
    await app().setProject(PROJECTS, "broken");
    await new Promise((r) => setTimeout(r, 450));
    expect(saved(url, "future")).toEqual(newer);
  });

  it("land a slow load on its own project, never on the one switched to meanwhile", async () => {
    const { url } = connect();
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("slow", {
      version: 1,
      "left-panel": { open: true },
      "mid-panel": { left: { open: true, tabs: [{ id: "s1", content: file("slow.py") }], active: "s1" }, right: { open: false, tabs: [] } },
      "right-panel": { open: true },
      focused: "left",
    });
    holdReads = (key) => key.endsWith("/slow");
    const slow = app().setProject(PROJECTS, "slow");
    await until(() => held.length === 1);
    holdReads = null;
    await app().setProject(PROJECTS, "fast");
    app().openTab(file("fast.py"), "left");
    held.splice(0).forEach((r) => r());
    await slow;
    expect(shownFiles()).toEqual(["fast.py"]);
    await app().setProject(PROJECTS, "slow");
    expect(shownFiles()).toEqual(["slow.py"]);
  });

  it("wait for the first load before taking an edit, so a project's file isn't replaced by its defaults", async () => {
    const { url } = connect();
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("alpha", {
      version: 1,
      "left-panel": { open: true },
      "mid-panel": { left: { open: true, tabs: [{ id: "k", content: file("kept.py") }], active: "k" }, right: { open: false, tabs: [] } },
      "right-panel": { open: true },
      focused: "left",
    });
    holdReads = () => true;
    const entering = app().setProject(PROJECTS, "alpha");
    await until(() => held.length === 1);
    app().openTab(file("new.py"), "left", "tab");
    expect(layoutOf(app()).left).toBeNull();
    holdReads = null;
    held.splice(0).forEach((r) => r());
    await entering;
    await until(() => shownFiles().length === 2);
    expect(shownFiles()).toEqual(["kept.py", "new.py"]);
  });

  it("keep an edit made while a re-entry's load is on its way, and save it over the file", async () => {
    const { url } = connect();
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("a.py"), "left");
    await app().setProject(PROJECTS, "beta");
    holdReads = (key) => key.endsWith("/alpha");
    const entering = app().setProject(PROJECTS, "alpha");
    await until(() => held.length === 1);
    // What was shown before is shown at once, and may be changed before the file comes.
    expect(shownFiles()).toEqual(["a.py"]);
    serviceAt(new URL(url).origin).set("alpha", { ...saved(url, "alpha"), focused: "right" });
    app().openTab(file("b.py"), "left", "tab");
    holdReads = null;
    held.splice(0).forEach((r) => r());
    await entering;
    expect(shownFiles()).toEqual(["a.py", "b.py"]);
    await until(() => saved(url, "alpha")?.["mid-panel"].left.tabs.length === 2);
  });

  it("save to the service each edit was made on, after switching to another", async () => {
    const one = connect();
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("one.py"), "left");
    const two = connect();
    await app().setProject(PROJECTS, "alpha");
    expect(shownFiles()).toEqual([]);
    await until(() => saved(one.url, "alpha") !== undefined);
    expect(saved(two.url, "alpha")).toBeUndefined();
    expect(requests.filter((r) => r.startsWith("PUT")).every((r) => r.includes(new URL(one.url).host))).toBe(true);
  });

  it("keep the right panel's session per project", async () => {
    connect();
    const app = useApp.getState;
    useApp.setState({ sessions: { "s-a": { id: "s-a", project: "alpha" } as never, "s-b": { id: "s-b", project: "beta" } as never } });
    await app().setProject(PROJECTS, "alpha");
    app().select("s-a");
    await until(() => selectedId(app()) === "s-a");
    await app().setProject(PROJECTS, "beta");
    expect(selectedId(app())).toBeUndefined();
    app().select("s-b");
    await until(() => selectedId(app()) === "s-b");
    await app().setProject(PROJECTS, "alpha");
    expect(selectedId(app())).toBe("s-a");
  });

  it("open another project's session in that project, not the one shown", async () => {
    connect();
    const app = useApp.getState;
    useApp.setState({ sessions: { "s-b": { id: "s-b", project: "beta" } as never } });
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("a.py"), "left");
    app().select("s-b");
    await until(() => app().scopes[app().activeId]?.[PROJECTS] === "beta" && areasOf(layoutOf(app())).length > 0);
    expect(layoutOf(app()).left!.tabs.map((t) => t.content.kind)).toEqual(["session"]);
    await app().setProject(PROJECTS, "alpha");
    expect(shownFiles()).toEqual(["a.py"]);
  });

  it("keep Dialogues' tabs per dialogue too", async () => {
    connect(DIALOGUES);
    const app = useApp.getState;
    const agent = (id: string): TabContent => ({ kind: "plugin", plugin: "dialogue", view: "agent", key: `${id}:a` });
    await app().setProject(DIALOGUES, "d-one");
    app().openTab(agent("d-one"), "left");
    app().openTab({ kind: "plugin", plugin: "dialogue", view: "file", key: "d-one:brief.md" }, "right", "tab");
    await app().setProject(DIALOGUES, "d-two");
    expect(areasOf(layoutOf(app()))).toEqual([]);
    await app().setProject(DIALOGUES, "d-one");
    expect(areasOf(layoutOf(app())).map(([area]) => area)).toEqual(["left", "right"]);
  });

  it("drop a restored session tab once the sessions show it has gone", async () => {
    const { url } = connect();
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("alpha", {
      version: 1,
      "left-panel": { open: true },
      "mid-panel": {
        left: { open: true, tabs: [{ id: "g", content: { kind: "session", sessionId: "gone" } }, { id: "l", content: { kind: "session", sessionId: "live" } }], active: "g" },
        right: { open: false, tabs: [] },
      },
      "right-panel": { open: true },
      focused: "left",
    });
    useApp.setState({ sessions: { live: { id: "live", project: "alpha" } as never }, sessionsLoaded: true });
    await app().setProject(PROJECTS, "alpha");
    expect(layoutOf(app()).left!.tabs.map((t) => t.id)).toEqual(["l"]);
    expect(layoutOf(app()).left!.active).toBe("l");
  });
});

describe("Sessions, the default project", () => {
  it("moves this browser's old Sessions tabs into its first layout.json, and gives them to no other project", async () => {
    const { id, url } = connect("sessions");
    const app = useApp.getState;
    const old = { left: { tabs: [{ id: "o", content: file("old.py") }], active: "o" }, right: null, focused: "left" };
    useApp.setState((s) => ({ layouts: { ...s.layouts, ...parseLayouts({ [id]: { sessions: old, [PROJECTS]: old } }) } }));
    app().setFeature(PROJECTS);
    await app().setProject(PROJECTS, "alpha");
    expect(shownFiles()).toEqual([]);
    app().setFeature("sessions");
    await until(() => saved(url, "default") !== undefined);
    expect(saved(url, "default")?.["mid-panel"].left.tabs.map((t: any) => t.content.kind)).toEqual(["file"]);
    expect(shownFiles()).toEqual(["start", "old.py"]);
    expect(app().layouts[id]?.sessions).toBeUndefined();
    expect(JSON.parse(localStorage.getItem("agentop.tabs") ?? "{}")[id]?.sessions).toBeUndefined();
  });

  it("keeps the old tabs, unsaved, when the service has no layout route", async () => {
    const { id } = connect("sessions");
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }));
    const old = { left: { tabs: [{ id: "o", content: file("old.py") }], active: "o" }, right: null, focused: "left" };
    useApp.setState((s) => ({ layouts: { ...s.layouts, ...parseLayouts({ [id]: { sessions: old } }) } }));
    useApp.getState().setFeature(PROJECTS);
    useApp.getState().setFeature("sessions");
    await until(() => !!useApp.getState().projectViews[id]?.default?.ready);
    expect(shownFiles()).toEqual(["start", "old.py"]);
    expect(useApp.getState().layouts[id]?.sessions).toBeDefined();
  });
});
