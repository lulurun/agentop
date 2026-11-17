import { beforeEach, describe, expect, it } from "vitest";
import { afterPrefix, defaultMode, KEYS, loadDraft, loadMode, saveDraft } from "./state";

describe("Input bar state", () => {
  beforeEach(() => localStorage.clear());

  it("shows on a touch screen until told otherwise", () => {
    expect(defaultMode(true)).toBe("bar");
    expect(defaultMode(false)).toBe("terminal");
    expect(loadMode(true)).toBe("bar");
    localStorage.setItem("agentop.input.mode", "terminal");
    expect(loadMode(true)).toBe("terminal");
    localStorage.setItem("agentop.input.mode", "nonsense");
    expect(loadMode(false)).toBe("terminal");
  });

  it("keeps each session's unsent text, and forgets it once empty", () => {
    saveDraft("a", "half a prompt");
    expect(loadDraft("a")).toBe("half a prompt");
    expect(loadDraft("b")).toBe("");
    saveDraft("a", "");
    expect(localStorage.getItem("agentop.input.draft.a")).toBeNull();
  });

  it("types the tmux prefix, then the text or key, with no Enter", () => {
    expect(afterPrefix("c")).toBe("\x02c");
    const bytes = Object.fromEntries(KEYS.map(([key, , , sent]) => [key, sent]));
    expect(afterPrefix(bytes["2"])).toBe("\x022");
    expect(bytes.up).toBe("\x1b[A");
  });
});
