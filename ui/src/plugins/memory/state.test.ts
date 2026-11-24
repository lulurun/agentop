import { describe, expect, it } from "vitest";
import { emptyLayout, focusArea, open, withPinnedTab, type Tab } from "../../tabs";
import { conversationOf, conversationTab, currentConversation, splitTerms } from "./state";

describe("conversation tabs", () => {
  it("keeps a session ID with colons whole", () => {
    expect(conversationOf(conversationTab("antigravity", "a:b"))).toEqual(["antigravity", "a:b"]);
  });
});

describe("search words", () => {
  it("splits words and keeps quoted phrases, as the service does", () => {
    expect(splitTerms(' sushi  "Tsukiji market" 鎌倉 ""')).toEqual(["sushi", "Tsukiji market", "鎌倉"]);
  });
});

describe("the right panel", () => {
  const search: Tab = { id: "home", content: { kind: "plugin", plugin: "memory", view: "search" }, pinned: true };
  const withConversation = open(withPinnedTab(emptyLayout(), search), conversationTab("claude", "s1"), "right", "replace", "t1");

  it("shows the conversation opened beside the search", () => {
    expect(withConversation.right?.tabs.map((t) => t.id)).toEqual(["t1"]);
    expect(currentConversation(withConversation)).toEqual(["claude", "s1"]);
  });

  it("keeps showing it while the search has focus", () => {
    expect(currentConversation(focusArea(withConversation, "left"))).toEqual(["claude", "s1"]);
  });

  it("shows nothing with only the search", () => {
    expect(currentConversation(withPinnedTab(emptyLayout(), search))).toBeNull();
  });
});
