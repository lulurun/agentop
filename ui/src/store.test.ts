import { describe, expect, it } from "vitest";
import type { Session } from "./api/client";
import { parseLayouts, sessionSummary } from "./store";

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

  it("drops a plugin tab whose view is gone, such as the old pinned Dialogues tab", () => {
    const agent = { id: "a", content: { kind: "plugin", plugin: "dialogue", view: "agent", key: "d1:a" } };
    const old = { id: "home", pinned: true, content: { kind: "plugin", plugin: "dialogue", view: "dialogues" } };
    const saved = { groups: [{ tabs: [old, agent], active: "home" }], focused: 0 };
    const tabs = parseLayouts({ home: { "plugin:dialogue": saved } }).home["plugin:dialogue"]!.left!.tabs;
    expect(tabs.map((t) => t.id)).toEqual(["a"]);
  });
});
