import { describe, expect, it } from "vitest";
import { conversationOf, conversationTab, splitTerms } from "./state";

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
