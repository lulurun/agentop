import { describe, expect, it } from "vitest";
import type { Session } from "./api/client";
import { activeCount, parseLayouts, sessionSummary } from "./store";

function session(fields: Partial<Session>): Session {
  return { id: "s", display_name: "agentop", description: "", managed: true, ai_title: null, ...fields } as Session;
}

describe("sessionSummary", () => {
  it("is the agent's own summary", () => {
    expect(sessionSummary(session({ ai_title: "Agentop cross-session delegation" }))).toBe("Agentop cross-session delegation");
  });

  it("prefers the user's description", () => {
    expect(sessionSummary(session({ ai_title: "From the agent", description: "fixing login" }))).toBe("fixing login");
  });

  it("is nothing when the summary is already the title", () => {
    expect(sessionSummary(session({ managed: false, ai_title: "Same text" }))).toBeNull();
    expect(sessionSummary(session({}))).toBeNull();
  });
});

describe("saved tabs", () => {
  const group = { tabs: [{ id: "t", content: { kind: "session", sessionId: "a" } }], active: "t" };
  const old = { groups: [group], focused: 0 };
  const layout = { left: group, right: null, focused: "left" };

  it("tabs saved before features become the Sessions tabs", () => {
    expect(parseLayouts({ home: old })).toEqual({ home: { sessions: layout } });
  });

  it("reads tabs per feature, and drops what isn't a layout", () => {
    expect(parseLayouts({ home: { sessions: layout, "plugin:x": { groups: "no" } } })).toEqual({ home: { sessions: layout } });
  });

  it("drops the History feature's tabs: History is in Sessions' left panel now", () => {
    const history = { groups: [{ tabs: [{ id: "home", pinned: true, content: { kind: "history" } }], active: "home" }], focused: 0 };
    expect(parseLayouts({ home: { sessions: old, history } })).toEqual({ home: { sessions: layout } });
  });

  it("drops a plugin tab whose view is gone", () => {
    const conversation = { id: "c", content: { kind: "plugin", plugin: "memory", view: "conversation", key: "claude:s1" } };
    const old = { id: "home", pinned: true, content: { kind: "plugin", plugin: "memory", view: "recent" } };
    const saved = { groups: [{ tabs: [old, conversation], active: "home" }], focused: 0 };
    const tabs = parseLayouts({ home: { "plugin:memory": saved } }).home["plugin:memory"]!.left!.tabs;
    expect(tabs.map((t) => t.id)).toEqual(["c"]);
  });

  it("gives no project another project feature's old tabs, which held every project's at once", () => {
    const tab = { tabs: [{ id: "f", content: { kind: "file", file: { sessionId: "a", source: "workdir", path: "x" } } }], active: "f" };
    const saved = { left: tab, right: null, focused: "left" };
    expect(parseLayouts({ home: { "plugin:projects": saved, "plugin:dialogue": saved, sessions: saved } })).toEqual({ home: { sessions: saved } });
  });
});

describe("activeCount", () => {
  const sessions = Object.fromEntries(
    [
      session({ id: "a" }),
      session({ id: "b", project: "default" }),
      session({ id: "c", project: "agentop" }),
      session({ id: "d", project: "agentop", counted: false }),
      session({ id: "e", project: "agentop", managed: false }),
    ].map((s) => [s.id, s]),
  );

  it("counts every project's managed, counted sessions", () => {
    expect(activeCount(sessions)).toBe(3);
  });

  it("counts one project's, with no project meaning default", () => {
    expect(activeCount(sessions, "default")).toBe(2);
    expect(activeCount(sessions, "agentop")).toBe(1);
    expect(activeCount(sessions, "other")).toBe(0);
  });
});
