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
  const layout = { groups: [{ tabs: [{ id: "t", content: { kind: "session", sessionId: "a" } }], active: "t" }], focused: 0 };

  it("tabs saved before workspaces become the Sessions tabs", () => {
    expect(parseLayouts({ home: layout })).toEqual({ home: { sessions: layout } });
  });

  it("reads tabs per workspace, and drops what isn't a layout", () => {
    expect(parseLayouts({ home: { sessions: layout, history: { groups: "no" } } })).toEqual({ home: { sessions: layout } });
  });
});
