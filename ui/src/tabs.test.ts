import { describe, expect, it } from "vitest";
import * as tabs from "./tabs";
import type { Layout, TabContent } from "./tabs";

const session = (sessionId: string): TabContent => ({ kind: "session", sessionId });
const file = (path: string, sessionId = "a"): TabContent => ({ kind: "file", file: { sessionId, source: "workdir", path } });

/** What each group shows, with the active tab in brackets, and which group has focus. */
function show(layout: Layout): string {
  const groups = layout.groups.map((g) =>
    g.tabs
      .map((t) => {
        const name = t.content.kind === "session" ? t.content.sessionId : t.content.file.path;
        return t.id === g.active ? `[${name}]` : name;
      })
      .join(" "),
  );
  return `${groups.join(" | ")} @${layout.focused}`;
}

let n = 0;
const id = () => `x${++n}`;

function build(...steps: [TabContent, tabs.OpenMode][]): Layout {
  return steps.reduce((layout, [content, mode]) => tabs.open(layout, content, mode, id()), tabs.emptyLayout());
}

describe("opening", () => {
  it("clicking a session switches the session tab instead of adding one", () => {
    expect(show(build([session("a"), "replace"], [session("b"), "replace"]))).toBe("[b] @0");
  });

  it("a new tab sits after the active one", () => {
    const layout = build([session("a"), "replace"], [session("b"), "tab"], [session("c"), "tab"]);
    expect(show(layout)).toBe("a b [c] @0");
    const back = tabs.activate(layout, 0, layout.groups[0].tabs[0].id);
    expect(show(tabs.open(back, session("d"), "tab", id()))).toBe("a [d] b c @0");
  });

  it("a session or file already open is shown, not opened twice", () => {
    const layout = build([session("a"), "replace"], [file("x.txt"), "tab"], [session("a"), "replace"], [file("x.txt"), "tab"]);
    expect(show(layout)).toBe("a [x.txt] @0");
  });

  it("clicking a session while a file is active opens a tab for it", () => {
    expect(show(build([file("x.txt"), "tab"], [session("b"), "replace"]))).toBe("x.txt [b] @0");
  });

  it("to the side makes a second group, then uses it", () => {
    const layout = build([session("a"), "replace"], [session("b"), "side"]);
    expect(show(layout)).toBe("[a] | [b] @1");
    expect(show(tabs.open(tabs.focusGroup(layout, 0), session("c"), "side", id()))).toBe("[a] | b [c] @1");
  });

  it("clicking a session lands in the focused group", () => {
    const layout = tabs.focusGroup(build([session("a"), "replace"], [session("b"), "side"]), 0);
    expect(show(tabs.open(layout, session("c"), "replace", id()))).toBe("[c] | [b] @0");
  });
});

describe("closing", () => {
  it("shows the right neighbour, or else the left one", () => {
    const layout = build([session("a"), "replace"], [session("b"), "tab"], [session("c"), "tab"]);
    const middle = tabs.activate(layout, 0, layout.groups[0].tabs[1].id);
    expect(show(tabs.close(middle, 0, layout.groups[0].tabs[1].id))).toBe("a [c] @0");
    expect(show(tabs.close(layout, 0, layout.groups[0].tabs[2].id))).toBe("a [b] @0");
  });

  it("an emptied group goes away and the other takes focus", () => {
    const layout = build([session("a"), "replace"], [session("b"), "side"]);
    expect(show(tabs.close(layout, 0, layout.groups[0].tabs[0].id))).toBe("[b] @0");
    expect(show(tabs.close(layout, 1, layout.groups[1].tabs[0].id))).toBe("[a] @0");
  });

  it("closing a session closes its tabs and its files' tabs everywhere", () => {
    const layout = build([session("a"), "replace"], [file("x.txt", "a"), "tab"], [session("b"), "side"], [session("a"), "tab"]);
    expect(show(tabs.closeSession(layout, "a"))).toBe("[b] @0");
  });
});

describe("splitting and moving", () => {
  it("split shows the focused tab in a second group", () => {
    expect(show(tabs.split(build([session("a"), "replace"]), id()))).toBe("[a] | [a] @1");
  });

  it("there are never more than two groups", () => {
    const two = tabs.split(build([session("a"), "replace"]), id());
    expect(tabs.split(two, id())).toBe(two);
  });

  it("moving a tab takes it to the other group", () => {
    const layout = build([session("a"), "replace"], [session("b"), "tab"]);
    expect(show(tabs.moveToOther(layout, 0, layout.groups[0].tabs[1].id))).toBe("[a] | [b] @1");
  });

  it("moving the last tab of a group removes the group", () => {
    const layout = build([session("a"), "replace"], [session("b"), "side"]);
    expect(show(tabs.moveToOther(layout, 1, layout.groups[1].tabs[0].id))).toBe("a [b] @0");
  });

  it("a lone tab stays where it is", () => {
    const layout = build([session("a"), "replace"]);
    expect(tabs.moveToOther(layout, 0, layout.groups[0].tabs[0].id)).toBe(layout);
  });
});

describe("saved layouts", () => {
  it("reads back what it saved", () => {
    const layout = build([session("a"), "replace"], [file("x.txt"), "side"]);
    expect(tabs.parseLayout(JSON.parse(JSON.stringify(layout)))).toEqual(layout);
  });

  it("refuses anything else", () => {
    expect(tabs.parseLayout(null)).toBeNull();
    expect(tabs.parseLayout({ groups: "no", focused: 0 })).toBeNull();
    expect(tabs.parseLayout({ groups: [{ tabs: [{ id: "t", content: { kind: "web" } }] }], focused: 0 })).toBeNull();
  });
});
