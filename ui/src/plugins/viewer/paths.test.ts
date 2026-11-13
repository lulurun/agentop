import { describe, expect, it } from "vitest";
import { isMarkdown, linkTarget, resolve } from "./paths";

describe("isMarkdown", () => {
  it("knows Markdown by its extension", () => {
    expect(isMarkdown("README.md")).toBe(true);
    expect(isMarkdown("docs/Guide.MARKDOWN")).toBe(true);
    expect(isMarkdown("notes.txt")).toBe(false);
    expect(isMarkdown("md")).toBe(false);
  });
});

describe("resolve", () => {
  it("resolves beside the file", () => {
    expect(resolve("docs/a.md", "b.md")).toBe("docs/b.md");
    expect(resolve("docs/a.md", "./img/x.png")).toBe("docs/img/x.png");
    expect(resolve("docs/a.md", "../README.md")).toBe("README.md");
    expect(resolve("a.md", "/docs/b.md")).toBe("docs/b.md");
  });

  it("goes nowhere above the root", () => {
    expect(resolve("a.md", "../b.md")).toBeNull();
    expect(resolve("docs/a.md", "../../b.md")).toBeNull();
    expect(resolve("docs/a.md", ".")).toBe("docs");
    expect(resolve("a.md", ".")).toBeNull();
  });
});

describe("linkTarget", () => {
  it("tells anchors, other sites, and files apart", () => {
    expect(linkTarget("docs/a.md", "#set-up")).toEqual({ kind: "anchor", id: "set-up" });
    expect(linkTarget("docs/a.md", "https://example.com/x")).toEqual({ kind: "external", href: "https://example.com/x" });
    expect(linkTarget("docs/a.md", "mailto:me@example.com").kind).toBe("external");
    expect(linkTarget("docs/a.md", "b.md#usage")).toEqual({ kind: "file", path: "docs/b.md", anchor: "usage" });
    expect(linkTarget("docs/a.md", "My%20Notes.md?plain=1")).toEqual({ kind: "file", path: "docs/My Notes.md", anchor: null });
  });

  it("is nothing when empty or above the root", () => {
    expect(linkTarget("a.md", "").kind).toBe("none");
    expect(linkTarget("a.md", "../up.md").kind).toBe("none");
  });
});
