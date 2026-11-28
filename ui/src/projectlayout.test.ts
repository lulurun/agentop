import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Connection } from "./connections";
import { openCoordinatorFor, openFile, selectDialogue } from "./plugins/dialogue/state";
import { openProject } from "./plugins/projects/index";
import { UI_PLUGINS } from "./plugins/registry";
import { currentFeature, layoutIn, layoutOf, panelsOf, parseLayouts, selectedId, splitOf, tabSession, useApp, widthsOf } from "./store";
import { areasOf, type TabContent } from "./tabs";

const PROJECTS = "plugin:projects" as const;
const DIALOGUES = "plugin:dialogue" as const;
/** The core's per-project layouts, on a project feature without fixed tabs: Projects' own are tested below. */
const LAYOUT = DIALOGUES;

/** Each connection's service: its projects' layout.json files, and replies held back when asked. */
const services = new Map<string, Map<string, unknown>>();
const requests: string[] = [];
/** Services older than panel widths, whose check refuses them. */
const olderService = new Set<string>();
let holdReads: ((url: string) => boolean) | null = null;
/** Any other request held back, by `METHOD origin/path`. */
let holdRequests: ((request: string) => boolean) | null = null;
const held: (() => void)[] = [];
/** Each service's sessions, for `GET /sessions` and the Coordinator routes. */
const rows = new Map<string, Record<string, unknown>[]>();

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
    if (!match) {
      if (holdRequests?.(`${method} ${origin}${pathname}`)) await new Promise<void>((resolve) => held.push(resolve));
      const listed = rows.get(origin) ?? [];
      if (method === "GET" && pathname === "/api/v2/sessions") return new Response(JSON.stringify(listed), { status: 200 });
      const coordinator = pathname.match(/^\/api\/v2\/plugins\/dialogue\/dialogues\/([^/]+)\/coordinator$/);
      const found = coordinator && listed.find((r) => r.project === decodeURIComponent(coordinator[1]));
      if (method === "POST" && found) return new Response(JSON.stringify(found), { status: 202 });
      return new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    }
    const files = serviceAt(origin);
    const project = decodeURIComponent(match[1]);
    if (method === "PUT") {
      const body = JSON.parse(String(init.body));
      if (olderService.has(origin) && (body["left-panel"].width ?? body["right-panel"].width ?? body["mid-panel"].split) !== undefined) {
        return new Response(JSON.stringify({ detail: "Extra inputs are not permitted" }), { status: 422 });
      }
      files.set(project, body);
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
  holdRequests = null;
  held.splice(0).forEach((r) => r());
  requests.length = 0;
  rows.clear();
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
  areasOf(layoutOf(useApp.getState())).flatMap(([, g]) => g.tabs.filter((t) => !t.fixed).map((t) => (t.content.kind === "file" ? t.content.file.path : t.content.kind)));
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
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("x.py"), "left");
    app().openTab(file("notes.md"), "right", "tab");
    app().focusArea(LAYOUT, "left");
    app().toggleRight();
    const before = layoutOf(app());

    await app().setProject(LAYOUT, "beta");
    expect(shownFiles()).toEqual([]);
    expect(panelsOf(app()).rightOpen).toBe(true);
    app().openTab(file("y.py"), "left");
    expect(shownFiles()).toEqual(["y.py"]);

    // Switching flushed alpha's edits to its file at once.
    expect(saved(url, "alpha")?.["mid-panel"].left.tabs[0].content.file.path).toBe("x.py");
    await app().setProject(LAYOUT, "alpha");
    expect(layoutOf(app())).toEqual(before);
    expect(shownFiles()).toEqual(["x.py", "notes.md"]);
    expect(panelsOf(app()).rightOpen).toBe(false);
    await until(() => saved(url, "beta") !== undefined);
    expect(saved(url, "beta")?.["mid-panel"].left.tabs.map((t: any) => t.content.file.path)).toEqual(["y.py"]);
  });

  it("read the file again on entry, so an edit by hand shows", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("x.py"), "left");
    await app().setProject(LAYOUT, "beta");
    const edited = structuredClone(saved(url, "alpha"))!;
    edited["mid-panel"].left.tabs[0].content.file.path = "by-hand.py";
    edited["left-panel"].open = false;
    serviceAt(new URL(url).origin).set("alpha", edited);
    await app().setProject(LAYOUT, "alpha");
    expect(shownFiles()).toEqual(["by-hand.py"]);
    expect(panelsOf(app()).leftOpen).toBe(false);
  });

  it("show the defaults for a missing or broken file, and don't write over a newer app's", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("broken", { version: 1, "mid-panel": "nope", focused: 7 });
    await app().setProject(LAYOUT, "broken");
    expect(shownFiles()).toEqual([]);
    const newer = { version: 9, whatever: true };
    serviceAt(new URL(url).origin).set("future", newer);
    await app().setProject(LAYOUT, "future");
    app().openTab(file("x.py"), "left");
    expect(shownFiles()).toEqual(["x.py"]);
    await app().setProject(LAYOUT, "broken");
    await new Promise((r) => setTimeout(r, 450));
    expect(saved(url, "future")).toEqual(newer);
  });

  it("land a slow load on its own project, never on the one switched to meanwhile", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("slow", {
      version: 1,
      "left-panel": { open: true },
      "mid-panel": { left: { open: true, tabs: [{ id: "s1", content: file("slow.py") }], active: "s1" }, right: { open: false, tabs: [] } },
      "right-panel": { open: true },
      focused: "left",
    });
    holdReads = (key) => key.endsWith("/slow");
    const slow = app().setProject(LAYOUT, "slow");
    await until(() => held.length === 1);
    holdReads = null;
    await app().setProject(LAYOUT, "fast");
    app().openTab(file("fast.py"), "left");
    held.splice(0).forEach((r) => r());
    await slow;
    expect(shownFiles()).toEqual(["fast.py"]);
    await app().setProject(LAYOUT, "slow");
    expect(shownFiles()).toEqual(["slow.py"]);
  });

  it("wait for the first load before taking an edit, so a project's file isn't replaced by its defaults", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    serviceAt(new URL(url).origin).set("alpha", {
      version: 1,
      "left-panel": { open: true },
      "mid-panel": { left: { open: true, tabs: [{ id: "k", content: file("kept.py") }], active: "k" }, right: { open: false, tabs: [] } },
      "right-panel": { open: true },
      focused: "left",
    });
    holdReads = () => true;
    const entering = app().setProject(LAYOUT, "alpha");
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
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("a.py"), "left");
    await app().setProject(LAYOUT, "beta");
    holdReads = (key) => key.endsWith("/alpha");
    const entering = app().setProject(LAYOUT, "alpha");
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
    const one = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("one.py"), "left");
    const two = connect(LAYOUT);
    await app().setProject(LAYOUT, "alpha");
    expect(shownFiles()).toEqual([]);
    await until(() => saved(one.url, "alpha") !== undefined);
    expect(saved(two.url, "alpha")).toBeUndefined();
    expect(requests.filter((r) => r.startsWith("PUT")).every((r) => r.includes(new URL(one.url).host))).toBe(true);
  });

  it("keep the right panel's session per project", async () => {
    connect(LAYOUT);
    const app = useApp.getState;
    useApp.setState({ sessions: { "s-a": { id: "s-a", project: "alpha" } as never, "s-b": { id: "s-b", project: "beta" } as never } });
    await app().setProject(LAYOUT, "alpha");
    app().select("s-a");
    await until(() => selectedId(app()) === "s-a");
    await app().setProject(LAYOUT, "beta");
    expect(selectedId(app())).toBeUndefined();
    app().select("s-b");
    await until(() => selectedId(app()) === "s-b");
    await app().setProject(LAYOUT, "alpha");
    expect(selectedId(app())).toBe("s-a");
  });

  it("open another project's session in that project, not the one shown", async () => {
    connect(LAYOUT);
    const app = useApp.getState;
    useApp.setState({ sessions: { "s-b": { id: "s-b", project: "beta" } as never } });
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("a.py"), "left");
    app().select("s-b");
    await until(() => app().scopes[app().activeId]?.[LAYOUT] === "beta" && areasOf(layoutOf(app())).length > 0);
    expect(layoutOf(app()).left!.tabs.map((t) => t.content.kind)).toEqual(["session"]);
    await app().setProject(LAYOUT, "alpha");
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
    const { url } = connect(LAYOUT);
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
    await app().setProject(LAYOUT, "alpha");
    expect(layoutOf(app()).left!.tabs.map((t) => t.id)).toEqual(["l"]);
    expect(layoutOf(app()).left!.active).toBe("l");
  });
});

describe("panel widths and the split", () => {
  it("are each project's own, saved in its file, and shown again on return", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().setPanelWidth("left", 340);
    app().setPanelWidth("right", 401.6);
    app().setSplit(LAYOUT, 0.3);
    expect(widthsOf(app())).toMatchObject({ leftWidth: 340, rightWidth: 402 });

    await app().setProject(LAYOUT, "beta");
    expect([widthsOf(app()).leftWidth, widthsOf(app()).rightWidth]).toEqual([undefined, undefined]);
    expect(splitOf(app(), LAYOUT)).toBeUndefined();
    app().setPanelWidth("left", 220);

    const alpha = saved(url, "alpha")!;
    expect(alpha["left-panel"]).toEqual({ open: true, width: 340 });
    expect(alpha["right-panel"]).toEqual({ open: true, width: 402 });
    expect(alpha["mid-panel"].split).toBe(0.3);
    await app().setProject(LAYOUT, "alpha");
    expect(widthsOf(app())).toMatchObject({ leftWidth: 340, rightWidth: 402 });
    expect(splitOf(app(), LAYOUT)).toBe(0.3);
    await until(() => saved(url, "beta") !== undefined);
    expect(saved(url, "beta")!["left-panel"]).toEqual({ open: true, width: 220 });
    // Sessions' own split and widths are untouched by the projects'.
    expect(splitOf(app(), "sessions")).toBeUndefined();
    expect(saved(url, "default")?.["left-panel"]?.width).toBeUndefined();
  });

  it("clamp a hand edit, ignore one that isn't a number, and write none until one is set", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    const files = serviceAt(new URL(url).origin);
    await app().setProject(LAYOUT, "plain");
    app().toggleLeft();
    await app().setProject(LAYOUT, "edited");
    expect(saved(url, "plain")!["left-panel"]).toEqual({ open: false });
    expect("split" in saved(url, "plain")!["mid-panel"]).toBe(false);

    const edited = structuredClone(saved(url, "plain"))!;
    edited["left-panel"] = { open: true, width: 5000 };
    edited["right-panel"] = { open: true, width: "wide" };
    edited["mid-panel"].split = 0.01;
    files.set("plain", edited);
    await app().setProject(LAYOUT, "plain");
    expect([widthsOf(app()).leftWidth, widthsOf(app()).rightWidth]).toEqual([800, undefined]);
    expect(splitOf(app(), LAYOUT)).toBe(0.1);
  });

  it("are left out of the file for a service older than them, rather than losing the save", async () => {
    const { url } = connect(LAYOUT);
    olderService.add(new URL(url).origin);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().openTab(file("x.py"), "left");
    app().setPanelWidth("left", 333);
    await app().setProject(LAYOUT, "beta");
    await until(() => saved(url, "alpha") !== undefined);
    expect(saved(url, "alpha")!["left-panel"]).toEqual({ open: true });
    expect(saved(url, "alpha")!["mid-panel"].left.tabs[0].content.file.path).toBe("x.py");
  });

  it("without a project are this browser's, and never reach a project's file or another connection", async () => {
    const { url } = connect(LAYOUT);
    const app = useApp.getState;
    await app().setProject(LAYOUT, "alpha");
    app().setPanelWidth("left", 300);
    await app().setProject(LAYOUT, null);
    expect(widthsOf(app()).leftWidth).not.toBe(300);
    app().setPanelWidth("left", 410);
    app().setSplit(LAYOUT, 0.7);
    expect(widthsOf(app()).leftWidth).toBe(410);
    expect(JSON.parse(localStorage.getItem("agentop.layout")!).leftWidth).toBe(410);
    expect(splitOf(app(), LAYOUT)).toBe(0.7);
    await until(() => saved(url, "alpha")?.["left-panel"].width === 300);

    const other = connect(LAYOUT);
    expect(splitOf(app(), LAYOUT)).toBeUndefined();
    await app().setProject(LAYOUT, "alpha");
    expect(widthsOf(app()).leftWidth).toBeUndefined();
    expect(saved(other.url, "alpha")).toBeUndefined();
  });
});

describe("Sessions, the default project", () => {
  it("moves this browser's old Sessions tabs into its first layout.json, and gives them to no other project", async () => {
    const { id, url } = connect("sessions");
    const app = useApp.getState;
    const old = { left: { tabs: [{ id: "o", content: file("old.py") }], active: "o" }, right: null, focused: "left" };
    useApp.setState((s) => ({ layouts: { ...s.layouts, ...parseLayouts({ [id]: { sessions: old, [LAYOUT]: old } }) } }));
    app().setFeature(LAYOUT);
    await app().setProject(LAYOUT, "alpha");
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
    useApp.getState().setFeature(LAYOUT);
    useApp.getState().setFeature("sessions");
    await until(() => !!useApp.getState().projectViews[id]?.default?.ready);
    expect(shownFiles()).toEqual(["start", "old.py"]);
    expect(useApp.getState().layouts[id]?.sessions).toBeDefined();
  });
});

/** The kinds and plugin keys of a view's tabs, whatever is shown. */
const tabsIn = (connection: string, feature: string, project?: string) =>
  areasOf(layoutIn(useApp.getState(), { connection, feature: feature as never, project })).flatMap(([, g]) =>
    g.tabs.map((t) => (t.content.kind === "plugin" ? `${t.content.view}:${t.content.key}` : t.content.kind === "session" ? `session:${t.content.sessionId}` : t.content.kind)),
  );
const release = () => held.splice(0).forEach((r) => r());
/** The focused area's active tab of a Projects project, as `tabsIn` names it. */
const activeIn = (connection: string, project: string) => {
  const layout = layoutIn(useApp.getState(), { connection, feature: PROJECTS, project });
  const tab = layout[layout.focused]?.tabs.find((t) => t.id === layout[layout.focused]!.active);
  return tab?.content.kind === "plugin" ? `${tab.content.view}:${tab.content.key}` : tab?.content.kind;
};

describe("a plugin's action that waits lands only in the view it began in", () => {
  it("Dialogues: picking a dialogue, then going to Sessions before its file comes, puts no agent in Sessions", async () => {
    const { id } = connect(DIALOGUES);
    const app = useApp.getState;
    holdReads = (key) => key.endsWith("/d-slow");
    const selecting = selectDialogue("d-slow");
    await until(() => held.length === 1);
    app().setFeature("sessions");
    await until(() => !!app().projectViews[id]?.default?.ready);
    holdReads = null;
    release();
    await selecting;
    await until(() => tabsIn(id, DIALOGUES, "d-slow").length === 2);
    expect(tabsIn(id, "sessions", "default")).toEqual(["start"]);
    expect(currentFeature(app())).toBe("sessions");
    expect(tabsIn(id, DIALOGUES, "d-slow")).toEqual(["agent:d-slow:a", "agent:d-slow:b"]);
  });

  it("Dialogues: picking another dialogue meanwhile keeps it shown, and the first one's agents stay its own", async () => {
    const { id } = connect(DIALOGUES);
    const app = useApp.getState;
    holdReads = (key) => key.endsWith("/d-slow");
    const selecting = selectDialogue("d-slow");
    await until(() => held.length === 1);
    holdReads = null;
    await selectDialogue("d-other");
    release();
    await selecting;
    await until(() => tabsIn(id, DIALOGUES, "d-slow").length === 2);
    expect(app().scopes[id]?.[DIALOGUES]).toBe("d-other");
    expect(tabsIn(id, DIALOGUES, "d-other")).toEqual(["agent:d-other:a", "agent:d-other:b"]);
    expect(shownFiles()).toEqual(["plugin", "plugin"]);
  });

  it("Dialogues: switching service meanwhile, to one with a dialogue of the same id, leaves that one alone", async () => {
    const one = connect(DIALOGUES);
    const app = useApp.getState;
    holdReads = (key) => key.endsWith("/d-same") && key.includes(new URL(one.url).host);
    const selecting = selectDialogue("d-same");
    await until(() => held.length === 1);
    holdReads = null;
    const two = connect(DIALOGUES);
    await app().setProject(DIALOGUES, "d-same");
    release();
    await selecting;
    await until(() => tabsIn(one.id, DIALOGUES, "d-same").length === 2);
    expect(tabsIn(two.id, DIALOGUES, "d-same")).toEqual([]);
    await until(() => saved(one.url, "d-same") !== undefined);
    await new Promise((r) => setTimeout(r, 450));
    expect(saved(two.url, "d-same")).toBeUndefined();
    expect(app().activeId).toBe(two.id);
  });

  it("Dialogues: a file asked for before going to Sessions opens in its dialogue, not in Sessions", async () => {
    const { id } = connect(DIALOGUES);
    const app = useApp.getState;
    holdReads = (key) => key.endsWith("/d-file");
    const opening = openFile("d-file", "deliverables/plan.md");
    await until(() => held.length === 1);
    app().setFeature("sessions");
    holdReads = null;
    release();
    await opening;
    await until(() => tabsIn(id, DIALOGUES, "d-file").length === 1);
    expect(tabsIn(id, DIALOGUES, "d-file")).toEqual(["file:d-file:deliverables/plan.md"]);
    expect(tabsIn(id, "sessions", "default")).toEqual(["start"]);
    expect(currentFeature(app())).toBe("sessions");
  });

  it("Dialogues: Open the Coordinator, answered after another dialogue is picked, opens it in its own dialogue", async () => {
    const { id, url } = connect(DIALOGUES);
    const app = useApp.getState;
    rows.set(new URL(url).origin, [{ id: "coord-1", project: "d-mine", display_name: "Coordinator", status: "running" }]);
    holdRequests = (r) => r.startsWith("POST") && r.endsWith("/coordinator");
    // What the right panel's button does, with the owner it captures.
    await app().setProject(DIALOGUES, "d-mine");
    const asking = openCoordinatorFor("d-mine");
    await until(() => held.length === 1);
    await app().setProject(DIALOGUES, "d-other");
    release();
    await asking;
    await until(() => tabsIn(id, DIALOGUES, "d-mine").length === 1);
    expect(tabsIn(id, DIALOGUES, "d-mine")).toEqual(["session:coord-1"]);
    expect(tabsIn(id, DIALOGUES, "d-other")).toEqual([]);
    expect(app().scopes[id]?.[DIALOGUES]).toBe("d-other");
  });

  it("Projects: the Coordinator asked for after going to Sessions is made active in its project, never in Sessions", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("x.py"), "left");
    await app().setProject(PROJECTS, "beta");
    holdReads = (key) => key.endsWith("/alpha");
    const opening = openProject(id, "alpha", true);
    await until(() => held.length === 1);
    app().setFeature("sessions");
    await until(() => !!app().projectViews[id]?.default?.ready);
    holdReads = null;
    release();
    await opening;
    expect(tabsIn(id, PROJECTS, "alpha")).toEqual(["coordinator:alpha", "file", "progress:alpha"]);
    expect(activeIn(id, "alpha")).toBe("coordinator:alpha");
    expect(tabsIn(id, "sessions", "default")).toEqual(["start"]);
    expect(currentFeature(app())).toBe("sessions");
  });

  it("Projects: picking another project meanwhile keeps it shown, and the first one's Coordinator is active only there", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("x.py"), "left");
    await app().setProject(PROJECTS, "beta");
    app().openTab(file("y.py"), "left");
    holdReads = (key) => key.endsWith("/alpha");
    const opening = openProject(id, "alpha", true);
    await until(() => held.length === 1);
    holdReads = null;
    await app().setProject(PROJECTS, "beta");
    release();
    await opening;
    expect(activeIn(id, "alpha")).toBe("coordinator:alpha");
    // The user's own pick wins: beta was entered last, and its tab stays active.
    await until(() => app().scopes[id]?.[PROJECTS] === "beta");
    expect(tabsIn(id, PROJECTS, "beta")).toEqual(["coordinator:beta", "file", "progress:beta"]);
    expect(activeIn(id, "beta")).toBe("file");
  });

  it("Projects: switching service meanwhile leaves the other service's project of the same name alone", async () => {
    const one = connect(PROJECTS);
    const app = useApp.getState;
    holdReads = (key) => key.endsWith("/alpha") && key.includes(new URL(one.url).host);
    const opening = openProject(one.id, "alpha", true);
    await until(() => held.length === 1);
    holdReads = null;
    const two = connect(PROJECTS);
    await app().setProject(PROJECTS, "alpha");
    app().openTab(file("two.py"), "left");
    release();
    await opening;
    expect(activeIn(one.id, "alpha")).toBe("coordinator:alpha");
    expect(activeIn(two.id, "alpha")).toBe("file");
    expect(app().activeId).toBe(two.id);
  });

  it("never opens another project's session in Sessions", () => {
    const { id } = connect("sessions");
    useApp.setState({ sessions: { "s-x": { id: "s-x", project: "alpha" } as never } });
    useApp.getState().select("s-x");
    useApp.getState().openTab({ kind: "session", sessionId: "s-x" }, "left", "tab");
    expect(tabsIn(id, "sessions", "default")).toEqual(["start"]);
    expect(currentFeature(useApp.getState())).toBe("sessions");
  });
});

describe("Projects' permanent Coordinator and Progress tabs", () => {
  const coordinator = (id: string, status = "running", created_at = 1, project = "alpha") =>
    ({ id, project, display_name: "Coordinator", managed: true, status, created_at, attachable: true }) as never;
  const owner = (connection: string, project = "alpha") => ({ connection, feature: PROJECTS, project });
  const posts = () => requests.filter((r) => r.startsWith("POST"));
  const fixedIn = (connection: string, project = "alpha") =>
    areasOf(layoutIn(useApp.getState(), owner(connection, project))).flatMap(([area, g]) =>
      g.tabs.filter((t) => t.fixed).map((t) => `${area} ${t.content.kind === "plugin" ? `${t.content.view}:${t.content.key}` : t.content.kind}`),
    );

  it("a new project has exactly one of each, in its own area, labelled for the project", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await openProject(id, "alpha");
    expect(fixedIn(id)).toEqual(["left coordinator:alpha", "right progress:alpha"]);
    const layout = layoutOf(app());
    expect([layout.left?.hidden, layout.right?.hidden]).toEqual([undefined, undefined]);
    const views = UI_PLUGINS.projects.feature!.views;
    expect(views.coordinator.tabLabel!("alpha")).toBe("Coordinator: alpha");
    expect(views.progress.tabLabel!("alpha")).toBe("Progress: alpha");
    // Another project gets its own pair, and alpha's are not in it.
    await openProject(id, "beta");
    expect(fixedIn(id, "beta")).toEqual(["left coordinator:beta", "right progress:beta"]);
    expect(posts()).toEqual([]);
  });

  it("a saved layout from before gains them, keeping its other tabs, sizes and panels, with no second Coordinator", async () => {
    const { id, url } = connect(PROJECTS);
    const app = useApp.getState;
    const progress = { kind: "plugin", plugin: "projects", view: "progress", key: "alpha" };
    serviceAt(new URL(url).origin).set("alpha", {
      version: 1,
      "left-panel": { open: false, width: 320 },
      "mid-panel": {
        left: { open: true, tabs: [{ id: "x", content: file("x.py") }, { id: "c", content: { kind: "session", sessionId: "coord-1" } }, { id: "p", content: progress }], active: "c" },
        right: { open: false, tabs: [{ id: "n", content: file("notes.md") }], active: "n" },
        split: 0.3,
      },
      "right-panel": { open: true, width: 410 },
      focused: "left",
    });
    useApp.setState({ sessions: { "coord-1": coordinator("coord-1") }, sessionsLoaded: true });
    await openProject(id, "alpha");
    const layout = layoutOf(app());
    expect(layout.left!.tabs.map((t) => (t.content.kind === "file" ? t.content.file.path : t.content.kind === "plugin" ? t.content.view : t.content.kind))).toEqual(["coordinator", "x.py"]);
    expect(layout.right!.tabs.map((t) => (t.content.kind === "file" ? t.content.file.path : t.content.kind === "plugin" ? t.content.view : t.content.kind))).toEqual(["progress", "notes.md"]);
    // The Coordinator's old session tab was the active one: its fixed tab is now.
    expect(activeIn(id, "alpha")).toBe("coordinator:alpha");
    expect(selectedId(app())).toBe("coord-1");
    // Entering shows both areas; the side panels and sizes are as saved.
    expect(layout.right!.hidden).toBeUndefined();
    expect(panelsOf(app()).leftOpen).toBe(false);
    expect(widthsOf(app())).toMatchObject({ leftWidth: 320, rightWidth: 410 });
    expect(splitOf(app(), PROJECTS)).toBe(0.3);
    await until(() => saved(url, "alpha")?.["mid-panel"].right.tabs.length === 2);
    expect(saved(url, "alpha")!["mid-panel"].left.tabs.map((t: any) => t.content.kind)).toEqual(["plugin", "file"]);
    expect(posts().filter((r) => !r.includes("/layout"))).toEqual([]);
  });

  it("can't be closed, moved or replaced in any ordinary way, while the other tabs can", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await openProject(id, "alpha");
    app().openTab(file("a.py"), "left", "tab");
    const left = () => layoutOf(app()).left!;
    const right = () => layoutOf(app()).right!;
    const fixedLeft = left().tabs[0].id;
    const fixedRight = right().tabs[0].id;

    app().closeTab(PROJECTS, "left", fixedLeft);
    app().closeTab(PROJECTS, "right", fixedRight);
    app().moveTab(PROJECTS, "left", fixedLeft);
    app().moveTab(PROJECTS, "right", fixedRight);
    app().activateTab(PROJECTS, "left", fixedLeft);
    app().closeActiveTab();
    app().activateTab(PROJECTS, "right", fixedRight);
    app().closeActiveTab();
    // Another project's Progress, opened in place of the one there, takes a tab of its own.
    app().openTab({ kind: "plugin", plugin: "projects", view: "progress", key: "beta" }, "right", "replace");
    // A plugin closing every tab it has, or the Progress Viewer button, never takes them away nor adds a second.
    app().closeTabs(PROJECTS, (c) => c.kind === "plugin" && c.view !== "progress");
    app().openTab({ kind: "plugin", plugin: "projects", view: "progress", key: "alpha" }, "right", "tab");
    app().openTab({ kind: "plugin", plugin: "projects", view: "coordinator", key: "alpha" }, "right", "tab");
    expect(fixedIn(id)).toEqual(["left coordinator:alpha", "right progress:alpha"]);

    // The ordinary file tab moves and closes as before.
    const a = left().tabs.find((t) => t.content.kind === "file")!;
    app().moveTab(PROJECTS, "left", a.id);
    expect(right().tabs.some((t) => t.id === a.id)).toBe(true);
    app().closeTab(PROJECTS, "right", a.id);
    expect(shownFiles()).toEqual(["plugin"]);
  });

  it("stands for whichever Coordinator the project has, and says so when it has none", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await openProject(id, "alpha", true);
    const fixed = layoutOf(app()).left!.tabs[0].content;
    expect(tabSession(app().sessions, fixed)).toBeUndefined();
    expect(selectedId(app())).toBeUndefined();

    useApp.setState({ sessions: { "coord-1": coordinator("coord-1", "starting") }, sessionsLoaded: true });
    expect(selectedId(app())).toBe("coord-1");
    // Replaced: the new one is running, the old one has exited.
    useApp.setState({ sessions: { "coord-1": coordinator("coord-1", "exited", 1), "coord-2": coordinator("coord-2", "running", 2), other: coordinator("other", "running", 3, "beta") } });
    expect(selectedId(app())).toBe("coord-2");
    // Opening it from the session list shows it in the Coordinator tab, not in a second one.
    app().openTab(file("b.py"), "left", "tab");
    app().select("coord-2");
    expect(layoutOf(app()).left!.tabs.filter((t) => t.content.kind === "session")).toEqual([]);
    expect(activeIn(id, "alpha")).toBe("coordinator:alpha");
    // A tab opened for a new Coordinator before the sessions listed it folds into the fixed tab once they do.
    app().openTabIn(owner(id), { kind: "session", sessionId: "coord-3" }, "left", "tab");
    expect(layoutOf(app()).left!.tabs.some((t) => t.content.kind === "session")).toBe(true);
    useApp.setState((s) => ({ sessions: { ...s.sessions, "coord-3": coordinator("coord-3", "running", 4) } }));
    app().tidyIn(owner(id));
    expect(layoutOf(app()).left!.tabs.some((t) => t.content.kind === "session")).toBe(false);
    expect(selectedId(app())).toBe("coord-3");
    // Gone altogether: the tab stays, standing for nothing.
    useApp.setState({ sessions: {} });
    app().tidyIn(owner(id));
    expect(fixedIn(id)).toEqual(["left coordinator:alpha", "right progress:alpha"]);
    expect(selectedId(app())).toBeUndefined();
    expect(posts().filter((r) => !r.includes("/layout"))).toEqual([]);
  });

  it("leave the area toggles alone after entering, until the project is entered again", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await openProject(id, "alpha");
    app().toggleArea("right");
    expect(layoutOf(app()).right!.hidden).toBe(true);
    app().openTab(file("x.py"), "left", "tab");
    app().tidyIn(owner(id));
    useApp.setState({ sessions: { "coord-1": coordinator("coord-1") }, sessionsLoaded: true });
    app().tidyIn(owner(id));
    expect(layoutOf(app()).right!.hidden).toBe(true);
    expect(fixedIn(id)).toEqual(["left coordinator:alpha", "right progress:alpha"]);
    // The right area's own toggle opens it; the left one can then be shut.
    app().toggleArea("right");
    app().toggleArea("left");
    expect([layoutOf(app()).left!.hidden, layoutOf(app()).right!.hidden]).toEqual([true, undefined]);
    await openProject(id, "beta");
    await openProject(id, "alpha");
    expect([layoutOf(app()).left!.hidden, layoutOf(app()).right!.hidden]).toEqual([undefined, undefined]);
  });

  it("keeps the New project buffer usable with no project chosen, without fixed tabs", async () => {
    const { id } = connect(PROJECTS);
    const app = useApp.getState;
    await app().setProject(PROJECTS, null);
    app().openTab({ kind: "plugin", plugin: "projects", view: "new" }, "left");
    expect(tabsIn(id, PROJECTS)).toEqual(["new:undefined"]);
    app().closeTab(PROJECTS, "left", layoutOf(app()).left!.tabs[0].id);
    expect(tabsIn(id, PROJECTS)).toEqual([]);
  });
});
