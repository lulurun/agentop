import { describe, expect, it } from "vitest";
import * as tabs from "./tabs";
import type { Area, Layout, TabContent } from "./tabs";

const session = (sessionId: string): TabContent => ({ kind: "session", sessionId });
const file = (path: string, sessionId = "a"): TabContent => ({ kind: "file", file: { sessionId, source: "workdir", path } });

/** Each area's tabs, the active one in brackets, `-` for an area that isn't there, and the active area. */
function show(layout: Layout): string {
  const area = (a: Area) => {
    const g = layout[a];
    if (!g) return "-";
    return g.tabs
      .map((t) => {
        const c = t.content;
        const name = c.kind === "session" ? c.sessionId : c.kind === "file" ? c.file.path : c.kind === "plugin" ? `${c.plugin}:${c.view}` : c.kind === "past" ? `past:${c.historyId}` : c.kind;
        return t.id === g.active ? `[${name}]` : name;
      })
      .join(" ");
  };
  return `${area("left")} | ${area("right")} @${layout.focused}`;
}

let n = 0;
const id = () => `x${++n}`;

function build(...steps: [TabContent, Area, tabs.OpenMode?][]): Layout {
  return steps.reduce((layout, [content, area, mode]) => tabs.open(layout, content, area, mode ?? "replace", id()), tabs.emptyLayout());
}

const tabId = (layout: Layout, area: Area, index: number) => layout[area]!.tabs[index].id;

describe("opening", () => {
  it("clicking a session switches the session tab instead of adding one", () => {
    expect(show(build([session("a"), "left"], [session("b"), "left"]))).toBe("[b] | - @left");
  });

  it("a new tab sits after the active one", () => {
    const layout = build([session("a"), "left"], [session("b"), "left", "tab"], [session("c"), "left", "tab"]);
    expect(show(layout)).toBe("a b [c] | - @left");
    const back = tabs.activate(layout, "left", tabId(layout, "left", 0));
    expect(show(tabs.open(back, session("d"), "left", "tab", id()))).toBe("a [d] b c | - @left");
  });

  it("a tab already open in either area is shown, not opened twice", () => {
    const layout = build([session("a"), "left"], [file("x.txt"), "right"], [session("a"), "left"], [file("x.txt"), "right"]);
    expect(show(layout)).toBe("[a] | [x.txt] @right");
    expect(show(tabs.open(layout, file("x.txt"), "left", "replace", id()))).toBe("[a] | [x.txt] @right");
  });

  it("clicking a session while a file is active opens a tab for it", () => {
    expect(show(build([file("x.txt"), "left"], [session("b"), "left"]))).toBe("x.txt [b] | - @left");
  });

  it("files always get a tab of their own", () => {
    expect(show(build([session("a"), "left"], [file("x.txt"), "right"], [file("y.txt"), "right"]))).toBe("[a] | x.txt [y.txt] @right");
  });
});

describe("the target area", () => {
  it("asked for by name, shows a session open in the other area there too", () => {
    const layout = build([session("a"), "left"], [session("b"), "left", "tab"]);
    expect(show(tabs.open(layout, session("b"), "right", "also", id()))).toBe("a [b] | [b] @right");
    expect(show(tabs.open(layout, { kind: "status" }, "right", "also", id()))).toBe("a [b] | [status] @right");
  });

  it("is made when it isn't there, and stays on its own side", () => {
    expect(show(build([file("x.txt"), "right"]))).toBe("- | [x.txt] @right");
    expect(show(build([file("x.txt"), "right"], [session("a"), "left"]))).toBe("[a] | [x.txt] @left");
  });

  it("a left action lands on the left even while the right area is active", () => {
    const layout = build([session("a"), "left"], [file("x.txt"), "right"]);
    expect(show(tabs.open(layout, session("c"), "left", "replace", id()))).toBe("[c] | [x.txt] @left");
    expect(show(tabs.open(layout, session("c"), "left", "tab", id()))).toBe("a [c] | [x.txt] @left");
  });

  it("a plugin's view replaces a tab of the same view in the area, active or not", () => {
    const agent = (key: string): TabContent => ({ kind: "plugin", plugin: "dialogue", view: "agent", key });
    const wizard: TabContent = { kind: "plugin", plugin: "dialogue", view: "new" };
    const layout = build([agent("d1:a"), "left"], [wizard, "left", "tab"]);
    expect(show(tabs.open(layout, agent("d2:a"), "left", "replace", id()))).toBe("[dialogue:agent] dialogue:new | - @left");
  });
});

describe("closing", () => {
  it("shows the right neighbour, or else the left one", () => {
    const layout = build([session("a"), "left"], [session("b"), "left", "tab"], [session("c"), "left", "tab"]);
    const middle = tabs.activate(layout, "left", tabId(layout, "left", 1));
    expect(show(tabs.close(middle, "left", tabId(layout, "left", 1)))).toBe("a [c] | - @left");
    expect(show(tabs.close(layout, "left", tabId(layout, "left", 2)))).toBe("a [b] | - @left");
  });

  it("an emptied area goes and the other becomes active; with neither, the background shows", () => {
    const layout = build([session("a"), "left"], [session("b"), "right"]);
    expect(show(tabs.close(layout, "left", tabId(layout, "left", 0)))).toBe("- | [b] @right");
    const one = tabs.close(layout, "right", tabId(layout, "right", 0));
    expect(show(one)).toBe("[a] | - @left");
    expect(show(tabs.closeActive(one))).toBe("- | - @left");
  });

  it("closing a session closes its tabs and its files' tabs everywhere", () => {
    const layout = build([session("a"), "left"], [file("x.txt", "a"), "left", "tab"], [session("b"), "right"]);
    expect(show(tabs.closeSession(layout, "a"))).toBe("- | [b] @right");
  });
});

describe("tab keys", () => {
  it("pick a tab of the active area by position", () => {
    const layout = build([session("a"), "left"], [session("b"), "left", "tab"], [file("x.txt"), "right"]);
    expect(show(tabs.activateAt(tabs.focusArea(layout, "left"), 0))).toBe("[a] b | [x.txt] @left");
    expect(tabs.activateAt(layout, 5)).toBe(layout);
  });

  it("focusing an area that isn't there does nothing", () => {
    const layout = build([session("a"), "left"]);
    expect(tabs.focusArea(layout, "right")).toBe(layout);
  });
});

describe("moving", () => {
  it("moving a tab takes it to the other area", () => {
    const layout = build([session("a"), "left"], [session("b"), "left", "tab"]);
    expect(show(tabs.moveToOther(layout, "left", tabId(layout, "left", 1)))).toBe("[a] | [b] @right");
  });

  it("moving an area's last tab removes the area", () => {
    const layout = build([session("a"), "left"], [session("b"), "right"]);
    expect(show(tabs.moveToOther(layout, "right", tabId(layout, "right", 0)))).toBe("a [b] | - @left");
    const lone = build([session("a"), "left"]);
    expect(show(tabs.moveToOther(lone, "left", tabId(lone, "left", 0)))).toBe("- | [a] @right");
  });
});

describe("shutting an area with its header toggle", () => {
  const two = () => build([session("a"), "left"], [session("b"), "right"]);
  const hidden = (layout: Layout) => tabs.AREAS.filter((a) => layout[a]?.hidden);

  it("keeps its tabs, moves the focus to the other area, and opens again as it was", () => {
    const shut = tabs.toggleArea(two(), "right");
    expect(hidden(shut)).toEqual(["right"]);
    expect(show(shut)).toBe("[a] | [b] @left");
    expect(tabs.shownAreas(shut).map(([a]) => a)).toEqual(["left"]);
    const back = tabs.toggleArea(shut, "right");
    expect(hidden(back)).toEqual([]);
    expect(show(back)).toBe("[a] | [b] @left");
  });

  it("never shuts the only area shown, or one that isn't there", () => {
    const lone = build([session("a"), "left"]);
    expect(tabs.toggleArea(lone, "left")).toBe(lone);
    expect(tabs.toggleArea(lone, "right")).toBe(lone);
    const shut = tabs.toggleArea(two(), "left");
    expect(tabs.toggleArea(shut, "right")).toBe(shut);
  });

  it("opens again when a tab is opened, shown, or focused there", () => {
    const shut = tabs.toggleArea(two(), "right");
    expect(hidden(tabs.open(shut, session("c"), "right", "replace", id()))).toEqual([]);
    expect(hidden(tabs.open(shut, session("b"), "left"))).toEqual([]);
    expect(show(tabs.focusArea(shut, "right"))).toBe("[a] | [b] @right");
    expect(hidden(tabs.focusArea(shut, "right"))).toEqual([]);
  });

  it("stays shut while a tab in it closes, and opens when the other area goes", () => {
    const layout = build([session("a"), "left"], [session("b"), "right"], [session("c"), "right", "tab"]);
    const shut = tabs.toggleArea(layout, "right");
    expect(hidden(tabs.closeSession(shut, "b"))).toEqual(["right"]);
    const alone = tabs.closeSession(shut, "a");
    expect(show(alone)).toBe("- | b [c] @right");
    expect(hidden(alone)).toEqual([]);
  });

});

describe("saved layouts", () => {
  it("reads back what it saved", () => {
    const layout = build([session("a"), "left"], [file("x.txt"), "right"]);
    expect(tabs.parseLayout(JSON.parse(JSON.stringify(layout)))).toEqual(layout);
  });

  it("reads the older groups, the first as the left area", () => {
    const t = (tid: string, content: TabContent) => ({ id: tid, content });
    const old = { groups: [{ tabs: [t("a", session("a"))], active: "a" }, { tabs: [t("b", file("x"))], active: "b" }], focused: 1 };
    expect(show(tabs.parseLayout(old)!)).toBe("[a] | [x] @right");
    expect(show(tabs.parseLayout({ groups: [], focused: 0 })!)).toBe("- | - @left");
  });

  it("refuses anything else", () => {
    expect(tabs.parseLayout(null)).toBeNull();
    expect(tabs.parseLayout({ groups: "no", focused: 0 })).toBeNull();
    expect(tabs.parseLayout({ left: { tabs: [{ id: "t", content: { kind: "web" } }], active: "t" }, right: null, focused: "left" })).toBeNull();
    expect(tabs.parseLayout({ left: null, right: null, focused: 0 })).toBeNull();
  });
});

describe("every feature's tabs", () => {
  const status: TabContent = { kind: "status" };
  const search: TabContent = { kind: "plugin", plugin: "memory", view: "search" };
  const past = (historyId: string): TabContent => ({ kind: "past", historyId });
  const dialogue: TabContent = { kind: "plugin", plugin: "dialogue", view: "dialogues" };

  it("a pinned tab can't be closed", () => {
    const layout = tabs.pinnedLayout(search);
    expect(tabs.close(layout, "left", "home")).toBe(layout);
  });

  it("Status opens in the left area, and only once", () => {
    const opened = tabs.open(build([session("a"), "left"], [file("x.txt"), "right"]), status, "left", "replace", id());
    expect(show(opened)).toBe("a [status] | [x.txt] @left");
    expect(show(tabs.open(tabs.focusArea(opened, "right"), status, "left", "replace", id()))).toBe("a [status] | [x.txt] @left");
  });

  it("only sessions and files open in both areas", () => {
    const layout = tabs.pinnedLayout(dialogue);
    expect(show(tabs.open(layout, dialogue, "right", "also", id()))).toBe("[dialogue:dialogues] | - @left");
    expect(tabs.canSplit(dialogue)).toBe(false);
  });

  it("a plugin's tabs close with it, pinned ones too", () => {
    const layout = tabs.open(tabs.pinnedLayout(dialogue), status, "left", "replace", id());
    expect(show(tabs.closeWhere(layout, (c) => c.kind === "plugin"))).toBe("[status] | - @left");
  });

  it("plugin tabs are told apart by view and key", () => {
    expect(tabs.sameContent(dialogue, { ...dialogue })).toBe(true);
    expect(tabs.sameContent(dialogue, { ...dialogue, key: "abc" })).toBe(false);
    expect(tabs.sameContent(status, search)).toBe(false);
  });

  it("a past session takes the place of the past session shown, not of a session", () => {
    const one = tabs.open(build([session("a"), "left"]), past("h1"), "left", "replace", id());
    expect(show(one)).toBe("a [past:h1] | - @left");
    expect(show(tabs.open(one, past("h2"), "left", "replace", id()))).toBe("a [past:h2] | - @left");
    expect(show(tabs.open(one, past("h2"), "left", "tab", id()))).toBe("a past:h1 [past:h2] | - @left");
    expect(tabs.canSplit(past("h1"))).toBe(false);
  });

  it("saved layouts keep every kind and the pin", () => {
    const layout = tabs.open(tabs.pinnedLayout(dialogue), status, "left", "replace", id());
    expect(tabs.parseLayout(JSON.parse(JSON.stringify(layout)))).toEqual(layout);
  });
});

describe("Sessions' start buffer", () => {
  it("opens first in the left area and can be closed", () => {
    const layout = tabs.withPinnedTab(tabs.emptyLayout());
    expect(layout.left?.tabs.map((t) => t.id)).toEqual(["start"]);
    expect(layout.left?.tabs[0].pinned).toBeFalsy();
    const opened = tabs.open(layout, session("a"), "left", "tab", "ta");
    expect(opened.left?.tabs.map((t) => t.id)).toEqual(["start", "ta"]);
    expect(tabs.close(opened, "left", "start").left?.tabs.map((t) => t.id)).toEqual(["ta"]);
  });

  it("is added to a saved layout once, and left where it was moved", () => {
    const saved = tabs.open(tabs.emptyLayout(), session("a"), "left", "replace", "ta");
    const withStart = tabs.withPinnedTab(saved);
    expect(withStart.left?.tabs.map((t) => t.id)).toEqual(["start", "ta"]);
    expect(withStart.left?.active).toBe("ta");
    expect(tabs.withPinnedTab(withStart)).toBe(withStart);
    const moved = tabs.moveToOther(withStart, "left", "start");
    expect(tabs.withPinnedTab(moved)).toBe(moved);
  });
});

describe("a project's file", () => {
  it("belongs to no session, and is told apart from a session's file of the same path", () => {
    const project: TabContent = { kind: "file", file: { sessionId: "alpha", source: "project", path: "results/a.md" } };
    const session: TabContent = { kind: "file", file: { sessionId: "alpha", source: "workdir", path: "results/a.md" } };
    expect(tabs.sessionOf(project)).toBeUndefined();
    expect(tabs.sameContent(project, session)).toBe(false);
    expect(tabs.closeSession({ left: { tabs: [{ id: "a", content: project }], active: "a" }, right: null, focused: "left" }, "alpha").left?.tabs)
      .toHaveLength(1);
  });
});

describe("fixed tabs", () => {
  const coordinator: tabs.Tab = { id: "fixed-coordinator", content: { kind: "plugin", plugin: "projects", view: "coordinator", key: "alpha" }, pinned: true, fixed: true };
  const progress: tabs.Tab = { id: "fixed-progress", content: { kind: "plugin", plugin: "projects", view: "progress", key: "alpha" }, pinned: true, fixed: true };
  const both = (layout: Layout) => tabs.withFixedTab(tabs.withFixedTab(layout, coordinator, "left"), progress, "right");

  it("go first in their areas of an empty layout, and the same layout comes back once they are", () => {
    const layout = both(tabs.emptyLayout());
    expect(show(layout)).toBe("[projects:coordinator] | [projects:progress] @left");
    expect(tabs.withFixedTab(layout, coordinator, "left")).toBe(layout);
    expect(tabs.withFixedTab(layout, progress, "right")).toBe(layout);
  });

  it("join a saved layout without changing its other tabs, which stays active, nor opening a shut area", () => {
    const saved = tabs.toggleArea(build([file("x.py"), "left"], [file("y.py"), "right", "tab"]), "right");
    const layout = both(saved);
    expect(show(layout)).toBe("projects:coordinator [x.py] | projects:progress [y.py] @left");
    expect(layout.right!.hidden).toBe(true);
    const shown = tabs.showAll(layout);
    expect(shown.right!.hidden).toBeUndefined();
    expect(tabs.showAll(shown)).toBe(shown);
  });

  it("are moved back to their own area and kept once, unpinned copies from a file included", () => {
    const stray = { ...progress, id: "p", pinned: undefined, fixed: undefined };
    const twice = { ...coordinator, id: "c2", pinned: undefined, fixed: undefined };
    const saved: Layout = {
      left: { tabs: [{ id: "f", content: file("x.py") }, stray, { ...coordinator, id: "c1", pinned: undefined, fixed: undefined }, twice], active: "p" },
      right: null,
      focused: "left",
    };
    const layout = both(saved);
    expect(show(layout)).toBe("[x.py] projects:coordinator | [projects:progress] @left");
    expect(layout.left!.tabs.find((t) => t.id === "c1")).toMatchObject({ pinned: true, fixed: true });
  });

  it("can't be closed, moved, or have another view put in their place; other tabs can", () => {
    const layout = tabs.open(both(tabs.emptyLayout()), file("a.py"), "left", "tab", "a");
    expect(tabs.close(layout, "left", coordinator.id)).toBe(layout);
    expect(tabs.closeActive(tabs.activate(layout, "right", progress.id))).toEqual(tabs.activate(layout, "right", progress.id));
    expect(tabs.moveToOther(layout, "left", coordinator.id)).toBe(layout);
    expect(tabs.moveToOther(layout, "right", progress.id)).toBe(layout);
    const beta = { kind: "plugin", plugin: "projects", view: "progress", key: "beta" } as TabContent;
    const replaced = tabs.open(tabs.activate(layout, "right", progress.id), beta, "right", "replace", "b");
    expect(show(replaced)).toBe("projects:coordinator [a.py] | projects:progress [projects:progress] @right");
    expect(show(tabs.moveToOther(layout, "left", "a"))).toBe("[projects:coordinator] | projects:progress [a.py] @right");
    expect(show(tabs.close(layout, "left", "a"))).toBe("[projects:coordinator] | [projects:progress] @left");
  });

  it("get a fresh id when a saved tab already has theirs", () => {
    const saved: Layout = { left: { tabs: [{ id: coordinator.id, content: file("x.py") }], active: coordinator.id }, right: null, focused: "left" };
    const layout = tabs.withFixedTab(saved, coordinator, "left");
    expect(new Set(layout.left!.tabs.map((t) => t.id)).size).toBe(2);
  });
});
