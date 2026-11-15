import { describe, expect, it } from "vitest";
import { emptyLayout, type Layout } from "../../tabs";
import { conversationOf, conversationTab, showConversation, splitTerms } from "./state";

const shown = (layout: Layout) => layout.groups.map((g) => g.tabs.find((t) => t.id === g.active)?.content);

describe("showing a conversation", () => {
  it("opens the first one in a tab, and the next in its place", () => {
    const first = showConversation(emptyLayout(), conversationTab("claude", "s1"));
    const second = showConversation(first, conversationTab("codex", "s2"));
    expect(second.groups[0].tabs).toHaveLength(1);
    expect(shown(second)).toEqual([conversationTab("codex", "s2")]);
    expect(conversationOf(shown(second)[0])).toEqual(["codex", "s2"]);
  });

  it("opens one in a tab of its own when asked, and shows one already open where it is", () => {
    let layout = showConversation(emptyLayout(), conversationTab("claude", "s1"));
    layout = showConversation(layout, conversationTab("claude", "s2"), true);
    expect(layout.groups[0].tabs).toHaveLength(2);
    layout = showConversation(layout, conversationTab("claude", "s1"));
    expect(layout.groups[0].tabs).toHaveLength(2);
    expect(shown(layout)).toEqual([conversationTab("claude", "s1")]);
  });

  it("keeps a session ID with colons whole", () => {
    expect(conversationOf(conversationTab("antigravity", "a:b"))).toEqual(["antigravity", "a:b"]);
  });
});

describe("search words", () => {
  it("splits words and keeps quoted phrases, as the service does", () => {
    expect(splitTerms(' sushi  "Tsukiji market" 鎌倉 ""')).toEqual(["sushi", "Tsukiji market", "鎌倉"]);
  });
});
